from watcher import Watcher
from context_builder import ContextBuilder
from tool_runner import ToolRunner


def main():
    watcher = Watcher()
    context_builder = ContextBuilder()
    tool_runner = ToolRunner()

    metrics = watcher.get_metrics()
    primary_event, detected_events = watcher.detect_events(metrics)
    context = context_builder.build_context(metrics, primary_event, detected_events)

    print("\n=== INCIDENT CONTEXT ===")
    for key, value in context.items():
        print(f"{key}: {value}")

    print("\n=== TOOL TESTS ===")
    print("\n[check_disk]")
    print(tool_runner.run_tool("check_disk"))

    print("\n[check_memory]")
    print(tool_runner.run_tool("check_memory"))

    print("\n[check_processes]")
    print(tool_runner.run_tool("check_processes"))

    print("\n[inspect_top_process]")
    print(tool_runner.run_tool("inspect_top_process"))


if __name__ == "__main__":
    main()