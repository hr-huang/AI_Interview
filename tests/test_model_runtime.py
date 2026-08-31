from __future__ import annotations

import unittest

from profile_agent.model_runtime import (
    ModelRuntimeConfig,
    ModelRuntimeRegistry,
    ModelScopedGraph,
    current_model_runtime_config,
)


def config(model: str, key: str = "test-key") -> ModelRuntimeConfig:
    return ModelRuntimeConfig(
        provider="qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=key,
        model=model,
    )


class RecordingGraph:
    def __init__(self) -> None:
        self.seen: list[str | None] = []

    def invoke(self, input, config=None, *args, **kwargs):
        runtime = current_model_runtime_config()
        self.seen.append(runtime.model if runtime is not None else None)
        return {"ok": True}


class ModelRuntimeTests(unittest.TestCase):
    def test_public_runtime_config_rejects_local_network_endpoint(self) -> None:
        for url in (
            "http://127.0.0.1:8000/v1",
            "https://127.0.0.1/v1",
            "https://localhost/v1",
            "https://169.254.169.254/latest/meta-data",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    ModelRuntimeConfig(
                        provider="openai_compatible",
                        base_url=url,
                        api_key="secret",
                        model="custom-model",
                    )

    def test_registry_keeps_assessment_configs_isolated(self) -> None:
        registry = ModelRuntimeRegistry()
        first = registry.create_session(config("model-a", "key-a"))
        second = registry.create_session(config("model-b", "key-b"))
        registry.bind_assessment("ast-a", first)
        registry.bind_assessment("ast-b", second)

        self.assertIsNone(current_model_runtime_config())
        with registry.use_for_assessment("ast-a"):
            active = current_model_runtime_config()
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.model, "model-a")
            self.assertEqual(active.api_key.get_secret_value(), "key-a")
        self.assertIsNone(current_model_runtime_config())

        with registry.use_for_assessment("ast-b"):
            active = current_model_runtime_config()
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.model, "model-b")
            self.assertEqual(active.api_key.get_secret_value(), "key-b")
        self.assertIsNone(current_model_runtime_config())

    def test_model_scoped_graph_uses_thread_id_binding_without_leaking_context(self) -> None:
        registry = ModelRuntimeRegistry()
        first = registry.create_session(config("model-a"))
        second = registry.create_session(config("model-b"))
        registry.bind_assessment("ast-a", first)
        registry.bind_assessment("ast-b", second)
        inner = RecordingGraph()
        graph = ModelScopedGraph(inner, registry)

        graph.invoke({}, {"configurable": {"thread_id": "ast-a"}})
        graph.invoke({}, {"configurable": {"thread_id": "ast-b"}})
        graph.invoke({}, {"configurable": {"thread_id": "ast-default"}})

        self.assertEqual(inner.seen, ["model-a", "model-b", None])
        self.assertIsNone(current_model_runtime_config())


if __name__ == "__main__":
    unittest.main()
