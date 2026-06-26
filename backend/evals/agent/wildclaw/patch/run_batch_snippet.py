    if args.agent_backend == "noesis":
        from src.agents.noesis import NoesisAgent
        backend: BaseAgent = NoesisAgent()
    # NOESIS_BACKEND_PATCH
