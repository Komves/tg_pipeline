from vesya_tools.secretary.handler import handle_secretary_message


async def handle_secretary_gateway(message, text, *, object_text=None):
    """
    Entry point for secretary mode (like analytics_agent.gateway)
    """

    try:
        result = await handle_secretary_message(
            message,
            text,
            object_text=object_text
        )
        if not isinstance(result, str) and result is not None:
            result = str(result)

        return result
        

    except Exception as e:
        print(f"[secretary_gateway] failed: {type(e).__name__}: {e}", flush=True)
        return None