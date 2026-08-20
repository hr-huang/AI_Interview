from langgraph.graph import START, END, StateGraph

from profile_agent.state.main_state import MainState

from profile_agent.nodes.input_processing import input_processing
from profile_agent.nodes.resume_understanding import resume_understanding
from profile_agent.nodes.job_understanding import job_understanding
from profile_agent.nodes.competency_modeling import competency_modeling
from profile_agent.nodes.interview_planner import interview_planner


def build_pre_interview_graph():

    builder = StateGraph(MainState)

    builder.add_node(
        "input_processing",
        input_processing,
    )

    builder.add_node(
        "resume_understanding",
        resume_understanding,
    )

    builder.add_node(
        "job_understanding",
        job_understanding,
    )

    builder.add_node(
        "competency_modeling",
        competency_modeling,
    )

    # 新增
    builder.add_node(
        "interview_planner",
        interview_planner,
    )

    builder.add_edge(
        START,
        "input_processing",
    )

    builder.add_edge(
        "input_processing",
        "resume_understanding",
    )

    builder.add_edge(
        "input_processing",
        "job_understanding",
    )

    builder.add_edge(
        [
            "resume_understanding",
            "job_understanding",
        ],
        "competency_modeling",
    )

    # 原来:
    # competency_modeling -> END

    # 现在:
    builder.add_edge(
        "competency_modeling",
        "interview_planner",
    )

    builder.add_edge(
        "interview_planner",
        END,
    )

    return builder.compile()


pre_interview_graph = build_pre_interview_graph()