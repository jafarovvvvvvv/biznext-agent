from __future__ import annotations

from app.llm.base import ToolSpec
from app.tools.base import AgentTool
from app.tools.calculate_metric import CalculateMetricTool
from app.tools.get_database_schema import GetDatabaseSchemaTool
from app.tools.get_table_metadata import GetTableMetadataTool
from app.tools.run_readonly_sql import RunReadonlySqlTool
from app.tools.submit_answer import SubmitAnswerTool

SUBMIT_ANSWER = "submit_answer"


def default_tools() -> dict[str, AgentTool]:
    tools: list[AgentTool] = [GetDatabaseSchemaTool(), GetTableMetadataTool(), CalculateMetricTool(),
                              RunReadonlySqlTool(), SubmitAnswerTool()]
    return {tool.name: tool for tool in tools}


def tool_specs(tools: dict[str, AgentTool]) -> list[ToolSpec]:
    return [ToolSpec(t.name, t.description, t.input_schema, t.strict) for t in tools.values()]
