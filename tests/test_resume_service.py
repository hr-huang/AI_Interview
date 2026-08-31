import unittest
from unittest.mock import patch

from profile_agent.schemas.resume_schema import ResumeProfile
from profile_agent.services.resume_service import parse_resume


class ResumeServicePromptTest(unittest.TestCase):
    def test_unknown_required_strings_use_empty_string_not_null(self) -> None:
        captured_messages = []

        def fake_structured(messages, _schema):
            captured_messages.extend(messages)
            return ResumeProfile()

        with patch(
            "profile_agent.services.resume_service.llm.structured",
            side_effect=fake_structured,
        ):
            parse_resume("负责过订单 Agent Workflow。")

        prompt = "\n".join(content for _, content in captured_messages)
        self.assertIn("company 和 role 未知时必须使用空字符串", prompt)
        self.assertIn("不能使用 null", prompt)


if __name__ == "__main__":
    unittest.main()
