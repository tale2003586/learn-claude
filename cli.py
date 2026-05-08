import asyncio
from pathlib import Path

from bootstrap import build_runtime


async def print_cli_message(message) -> None:
    if message.channel == "cli":
        print(message.content)


async def main_async() -> None:
    print(f"Agent Harness - {Path.cwd()}")
    print("Type 'q' to quit.\n")

    runtime = build_runtime()
    runtime.bus.subscribe_outbound("cli", print_cli_message)
    runtime.start()

    try:
        while True:
            try:
                query = await asyncio.to_thread(input, "\033[36m>> \033[0m")
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break

            if not query.strip():
                continue
            if query.strip().lower() in ("q", "quit", "exit"):
                break

            await runtime.submit_user_message(query)
            await runtime.run_once()
            print("---")
    finally:
        await runtime.stop()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
