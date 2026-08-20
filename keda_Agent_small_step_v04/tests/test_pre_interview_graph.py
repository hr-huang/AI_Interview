import unittest

from profile_agent.graphs.pre_interview import pre_interview_graph


class PreInterviewGraphSmokeTest(unittest.TestCase):
    def test_pre_interview_graph_has_no_runtime_initialization_node(self) -> None:
        node_names = list(pre_interview_graph.get_graph().nodes)
        runtime_nodes = [
            name for name in node_names if "runtime" in name.lower()
        ]

        self.assertEqual(runtime_nodes, [])


if __name__ == "__main__":
    unittest.main()
