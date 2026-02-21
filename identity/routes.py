"""
Semantic route definitions.
Add, remove, or modify intents here.
"""

from semantic_router import Route


def build_routes() -> list[Route]:
    """Return all semantic routes."""

    politics = Route(
        name="politics",
        utterances=[
            "isn't politics the best thing ever",
            "why don't you tell me about your political opinions",
            "don't you just love the president",
            "they're going to destroy this country!",
            "they will save the country!",
        ],
    )

    chitchat = Route(
        name="chitchat",
        utterances=[
            "how's the weather today?",
            "how are things going?",
            "lovely weather today",
            "the weather is horrendous",
            "let's go to the chippy",
        ],
    )

    technical_support = Route(
        name="technical_support",
        utterances=[
            "my application is crashing",
            "I'm getting an error message",
            "how do I reset my password",
            "the system is running slow",
            "I can't connect to the service",
            "help me troubleshoot this issue",
        ],
    )

    billing = Route(
        name="billing",
        utterances=[
            "I have a question about my invoice",
            "how do I update my payment method",
            "can I get a refund",
            "what are your pricing plans",
            "I was charged incorrectly",
            "when is my next payment due",
        ],
    )

    product_info = Route(
        name="product_info",
        utterances=[
            "what features does your product have",
            "tell me about your enterprise plan",
            "do you have an API",
            "what integrations do you support",
            "is there a free tier available",
            "how does your product compare to competitors",
        ],
    )

    return [politics, chitchat, technical_support, billing, product_info]
