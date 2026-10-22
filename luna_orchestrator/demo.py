"""Run with: python -m luna_orchestrator.demo"""

from .core import Orchestrator


def main() -> None:
    orchestrator = Orchestrator()
    orchestrator.register("echo", lambda message: message.upper())
    task = orchestrator.submit("echo", "hello luna", sensitive=True)
    print("Submitted:", task.status.value)
    orchestrator.approve(task.id)
    result = orchestrator.execute(task.id)
    print("Result:", result.result)
    print("Status:", result.status.value)


if __name__ == "__main__":
    main()
