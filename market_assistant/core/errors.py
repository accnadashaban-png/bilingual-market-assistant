"""Typed errors. Each one carries a safe message for the user in Arabic and English,
so internal details (SQL, stack traces) never reach the chat."""
from __future__ import annotations


class AssistantError(Exception):
    code = "internal_error"
    message_en = "Something went wrong on my side. Please try again."
    message_ar = "حصلت مشكلة عندي، جرّب تاني بعد شوية."

    def user_message(self, language: str) -> str:
        return self.message_ar if language == "ar" else self.message_en


class UnsafeQueryError(AssistantError):
    code = "unsafe_query"
    message_en = "I can only read market data, so I can't run that request."
    message_ar = "أنا بقرأ بيانات السوق بس، فمقدرش أنفّذ الطلب ده."


class QueryTimeoutError(AssistantError):
    code = "query_timeout"
    message_en = "That question needs a query that's too heavy. Try narrowing it (one market, sector or period)."
    message_ar = "السؤال ده محتاج استعلام تقيل. جرّب تحدده أكتر (سوق أو قطاع أو فترة واحدة)."


class NotFoundError(AssistantError):
    code = "not_found"
    message_en = "I couldn't find that company in the data."
    message_ar = "ملقتش الشركة دي في البيانات."


class NotEnoughHistoryError(AssistantError):
    code = "not_enough_history"
    message_en = "The data doesn't go back far enough for that period. Try a shorter one, like 3 months."
    message_ar = "البيانات مش راجعة لورا كفاية للفترة دي. جرّب فترة أقصر، زي 3 شهور."


class PlanningError(AssistantError):
    code = "planning_failed"
    message_en = "I didn't quite get that. Could you rephrase it?"
    message_ar = "مفهمتش السؤال كويس، ممكن تقوله بطريقة تانية؟"


class LLMUnavailableError(AssistantError):
    code = "llm_unavailable"
    message_en = "The language model isn't reachable right now. Please try again in a moment."
    message_ar = "نموذج اللغة مش متاح دلوقتي، جرّب كمان شوية."


class RateLimitedError(AssistantError):
    code = "rate_limited"
    message_en = "You're sending messages quickly. Please wait a few seconds."
    message_ar = "بتبعت رسايل بسرعة، استنى ثواني وجرّب تاني."
