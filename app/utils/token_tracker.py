"""Token usage tracking utilities for monitoring LLM prompt costs."""

from __future__ import annotations

from dataclasses import dataclass

from app.utils.logger import setup_logging

logger = setup_logging(__name__)


@dataclass
class TokenUsage:
    query_tokens: int
    schema_tokens: int
    generated_tokens: int
    response_tokens: int
    db_flag: str
    total_input_tokens: int
    total_output_tokens: int
    cost_usd: float


class TokenTracker:
    """Naive token tracker using whitespace-based token counting."""

    INPUT_COST_PER_TOKEN = 0.59 / 1_000_000
    OUTPUT_COST_PER_TOKEN = 0.79 / 1_000_000

    def __init__(self) -> None:
        self.history: list[TokenUsage] = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def count_tokens(self, text: str | None) -> int:
        if not text:
            return 0
        return len(text.split())

    def track_request(
        self,
        query: str,
        schema_text: str,
        generated_sql: str,
        response_text: str,
        db_flag: str,
    ) -> TokenUsage:
        query_tokens = self.count_tokens(query)
        schema_tokens = self.count_tokens(schema_text)
        generated_tokens = self.count_tokens(generated_sql)
        response_tokens = self.count_tokens(response_text)

        total_input = query_tokens + schema_tokens
        total_output = generated_tokens + response_tokens

        self.total_input_tokens += total_input
        self.total_output_tokens += total_output

        usage = TokenUsage(
            query_tokens=query_tokens,
            schema_tokens=schema_tokens,
            generated_tokens=generated_tokens,
            response_tokens=response_tokens,
            db_flag=db_flag,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            cost_usd=self._calculate_cost(total_input, total_output),
        )
        self.history.append(usage)

        logger.info(
            "Token usage | input=%d output=%d cost=$%.4f",
            total_input,
            total_output,
            usage.cost_usd,
        )

        return usage

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens * self.INPUT_COST_PER_TOKEN + output_tokens * self.OUTPUT_COST_PER_TOKEN


def get_token_tracker() -> TokenTracker:
    """Return a shared token tracker instance."""

    if not hasattr(get_token_tracker, "_instance"):
        get_token_tracker._instance = TokenTracker()  # type: ignore[attr-defined]
    return get_token_tracker._instance  # type: ignore[attr-defined]
