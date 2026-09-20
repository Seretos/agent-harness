import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        # Dispatched before importing mcp/the lib: hooks spawn once per tool call.
        from harness_plugin.hooks.write_context import main as hook_main

        sys.exit(hook_main())
    from harness_plugin.server import main

    main()
