import os

from dotenv import load_dotenv

from plugins.scheduler.reports import ScheduledReportService
from plugins.scheduler.store import ScheduleStore


class SchedulerWorker:
    def __init__(
        self,
        *,
        store=None,
        reports=None,
        scheduler=None,
        cron_trigger=None,
    ) -> None:
        self.store = store or ScheduleStore()
        self.reports = reports or ScheduledReportService(store=self.store)
        if scheduler is None or cron_trigger is None:
            try:
                from apscheduler.schedulers.blocking import BlockingScheduler
                from apscheduler.triggers.cron import CronTrigger
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "APScheduler is required. Run: pip install -r requirements.txt"
                ) from exc
            scheduler = scheduler or BlockingScheduler(
                timezone=os.environ.get("SCHEDULER_TIMEZONE", "Asia/Shanghai"),
            )
            cron_trigger = cron_trigger or CronTrigger
        self.scheduler = scheduler
        self.cron_trigger = cron_trigger
        self._signatures: dict[int, tuple] = {}

    def reconcile(self) -> None:
        schedules = self.store.list_schedules(enabled_only=True)
        active_ids = {schedule["id"] for schedule in schedules}

        for schedule_id in set(self._signatures) - active_ids:
            self._remove_job(schedule_id)
            self._signatures.pop(schedule_id, None)

        for schedule in schedules:
            signature = (
                schedule["hour"],
                schedule["minute"],
                schedule["timezone"],
            )
            if self._signatures.get(schedule["id"]) == signature:
                continue
            self.scheduler.add_job(
                self.run_schedule,
                trigger=self.cron_trigger(
                    hour=schedule["hour"],
                    minute=schedule["minute"],
                    timezone=schedule["timezone"],
                ),
                args=[schedule["id"]],
                id=self._job_id(schedule["id"]),
                name=schedule["name"],
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=3600,
            )
            self._signatures[schedule["id"]] = signature
            print(
                f"Scheduled #{schedule['id']} {schedule['name']} at "
                f"{schedule['hour']:02d}:{schedule['minute']:02d} "
                f"{schedule['timezone']}"
            )

    def run_schedule(self, schedule_id: int) -> None:
        result = self.reports.run(schedule_id)
        print(f"Schedule #{schedule_id}: {result}")

    def run_forever(self) -> None:
        interval = max(5, int(os.environ.get("SCHEDULER_RECONCILE_SECONDS", "30")))
        self.scheduler.add_job(
            self.reconcile,
            trigger="interval",
            seconds=interval,
            id="scheduler-reconcile",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.reconcile()
        print(f"taleclaw scheduler worker started; reconciling every {interval}s")
        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            print("taleclaw scheduler worker stopped")

    def _remove_job(self, schedule_id: int) -> None:
        job_id = self._job_id(schedule_id)
        if self.scheduler.get_job(job_id) is not None:
            self.scheduler.remove_job(job_id)
            print(f"Removed schedule #{schedule_id}")

    def _job_id(self, schedule_id: int) -> str:
        return f"scheduled-search:{schedule_id}"


def main() -> None:
    load_dotenv(override=True)
    SchedulerWorker().run_forever()


if __name__ == "__main__":
    main()
