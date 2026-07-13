"""
Cortex API — Core Billing Engine

Simplified for BYOK model:
- All LLM usage is BYOK — user pays provider directly
- Subscription ($10/mo, 899 INR) covers: Mistral OCR, SiliconFlow Embeddings, Web Search
- No margin logic — flat monthly fee
"""


class CoreBillingEngine:
    """
    Simplified billing engine for BYOK model.
    
    Subscription covers:
    - Mistral OCR: Image text extraction
    - SiliconFlow embeddings: Semantic search
    - Web search: SerpAPI/DuckDuckGo
    
    All LLM usage is BYOK — user pays provider directly.
    """

    def get_plan_info(self) -> dict:
        """Return current plan information."""
        return {
            "plans": {
                "pro": {
                    "name": "Pro",
                    "price_usd": 10.00,
                    "price_inr": 899.00,
                    "period": "month",
                    "features": [
                        "Mistral OCR (image text extraction)",
                        "SiliconFlow embeddings (semantic search)",
                        "Web search (SerpAPI/DuckDuckGo)",
                        "All LLM models via BYOK",
                    ],
                },
                "pro_yearly": {
                    "name": "Pro (Yearly)",
                    "price_usd": 80.00,
                    "price_inr": 6999.00,
                    "period": "year",
                    "savings": "33%",
                    "features": [
                        "Mistral OCR (image text extraction)",
                        "SiliconFlow embeddings (semantic search)",
                        "Web search (SerpAPI/DuckDuckGo)",
                        "All LLM models via BYOK",
                    ],
                },
            },
            "llm_pricing": "BYOK - you pay provider directly",
        }


# Module-level singleton
billing_engine = CoreBillingEngine()
