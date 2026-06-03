import os
from pathlib import Path

from dotenv import load_dotenv

from gateway.telegram.store import TelegramGatewayStore
from plugins.scheduler.reports import ScheduledReportService
from plugins.scheduler.store import ScheduleStore


class SchedulerWorker:
    def __init__(
        self,
        *,
        store=None,
        reports=None,
        agent_runner=None,
        agent_runner_factory=None,
        notifier=None,
        scheduler=None,
        cron_trigger=None,
    ) -> None:
        self.store = store or ScheduleStore()
        self.reports = reports or ScheduledReportService(store=self.store)
        self.agent_runner = agent_runner
        self.agent_runner_factory = agent_runner_factory or _build_agent_runner
        self.notifier = notifier if notifier is not None else TelegramScheduleNotifier()
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
        schedules = [
            schedule
            for schedule in self.store.list_schedules(enabled_only=True)
            if (
                schedule.get("schedule_type", "workflow") != "agent"
                or schedule.get("approval_status") == "active"
            )
        ]
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
        schedule = self.store.get(schedule_id)
        if schedule.get("schedule_type", "workflow") == "agent":
            if self.agent_runner is None:
                self.agent_runner = self.agent_runner_factory()
            result = self.agent_runner.run(schedule_id)
        else:
            result = self.reports.run(schedule_id)
        self.notifier.notify(schedule, result)
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


def _build_agent_runner():
    from core.bootstrap import build_runtime
    from plugins.scheduler.plugin import SchedulerPlugin

    runtime = build_runtime()
    for plugin in runtime.loop.plugin_manager.plugins:
        if isinstance(plugin, SchedulerPlugin) and plugin.agent_runner is not None:
            return plugin.agent_runner
    raise RuntimeError("Scheduled agent runner could not be initialized.")


class TelegramScheduleNotifier:
    def __init__(
        self,
        *,
        store=None,
        workspace: str | Path | None = None,
    ) -> None:
        self.workspace = Path(workspace or Path.cwd()).resolve()
        self._store = store

    def notify(self, schedule: dict, result: dict) -> None:
        chat_ids = _notification_chat_ids()
        if not chat_ids:
            return
        text = _notification_text(schedule, result, workspace=self.workspace)
        document_path = _report_document_path(
            str(result.get("report_path") or ""),
            workspace=self.workspace,
        )
        for chat_id in chat_ids:
            self.store.enqueue_message(
                chat_id=chat_id,
                text=text,
                source="scheduler",
                metadata={
                    "schedule_id": schedule.get("id"),
                    "run_id": result.get("run_id"),
                    "status": result.get("status"),
                    "report_path": result.get("report_path"),
                },
            )
            if document_path:
                self.store.enqueue_document(
                    chat_id=chat_id,
                    document_path=document_path,
                    caption=(
                        f"定时任务报告：{schedule.get('name', 'unnamed')}\n"
                        f"{document_path}"
                    ),
                    source="scheduler",
                    metadata={
                        "schedule_id": schedule.get("id"),
                        "run_id": result.get("run_id"),
                        "status": result.get("status"),
                        "report_path": result.get("report_path"),
                    },
                )

    @property
    def store(self):
        if self._store is None:
            self._store = TelegramGatewayStore(self.workspace / ".gateway" / "telegram.db")
        return self._store


def _notification_chat_ids() -> list[int]:
    explicit = os.environ.get("TELEGRAM_NOTIFY_CHAT_IDS", "").strip()
    if explicit:
        return _parse_int_list(explicit, allow_star=False)
    mapped = []
    user_map = os.environ.get("TELEGRAM_USER_MAP", "").strip()
    if user_map:
        import json

        try:
            payload = json.loads(user_map)
            if isinstance(payload, dict):
                mapped.extend(_parse_int_list(",".join(payload.keys()), allow_star=False))
        except json.JSONDecodeError:
            pass
    allowed = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    mapped.extend(_parse_int_list(allowed, allow_star=False))
    seen = set()
    result = []
    for item in mapped:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _parse_int_list(value: str, *, allow_star: bool) -> list[int]:
    result = []
    for item in str(value or "").split(","):
        cleaned = item.strip()
        if not cleaned or (cleaned == "*" and not allow_star):
            continue
        try:
            parsed = int(cleaned)
        except ValueError:
            continue
        if parsed > 0:
            result.append(parsed)
    return result


def _notification_text(schedule: dict, result: dict, *, workspace: Path) -> str:
    status = result.get("status", "unknown")
    report_path = str(result.get("report_path") or "")
    lines = [
        f"定时任务完成：{schedule.get('name', 'unnamed')}",
        f"状态：{status}",
    ]
    if result.get("error"):
        lines.append(f"错误：{result['error']}")
    if report_path:
        lines.append(f"报告：{report_path}")
    content = _report_excerpt(report_path, workspace=workspace)
    if content:
        lines.extend(["", content])
    return "\n".join(lines).strip()


def _report_excerpt(report_path: str, *, workspace: Path) -> str:
    if not report_path:
        return ""
    path = (workspace / report_path).resolve()
    if not path.is_file() or not path.is_relative_to(workspace):
        return ""
    max_chars = _env_int("TELEGRAM_NOTIFY_MAX_CHARS", default=3500, minimum=500)
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n[报告内容已截断，可到 Web 文件区查看完整报告。]"


def _report_document_path(report_path: str, *, workspace: Path) -> str:
    if not _env_bool("TELEGRAM_NOTIFY_SEND_REPORT_FILE", default=True):
        return ""
    if not report_path:
        return ""
    path = (workspace / report_path).resolve()
    if not path.is_file() or not path.is_relative_to(workspace):
        return ""
    max_bytes = _env_int(
        "TELEGRAM_NOTIFY_DOCUMENT_MAX_BYTES",
        default=10 * 1024 * 1024,
        minimum=1,
    )
    if path.stat().st_size > max_bytes:
        return ""
    return path.relative_to(workspace).as_posix()


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, *, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def main() -> None:
    load_dotenv(override=True)
    SchedulerWorker().run_forever()


if __name__ == "__main__":
    main()
