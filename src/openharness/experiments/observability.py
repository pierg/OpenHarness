from openharness.observability.langfuse import (
    configure_local_langfuse,
    langfuse_agent_env_for_docker,
)


def setup_local_langfuse(docker_compatible: bool = False) -> dict[str, str]:
    """Setup local Langfuse environment variables for examples."""
    if docker_compatible:
        return langfuse_agent_env_for_docker()

    configure_local_langfuse()
    return {}
