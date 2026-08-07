class RagTokenizer:
    def tokenize(self, text: str) -> str:
        return text or ""

    def fine_grained_tokenize(self, text: str) -> str:
        return text or ""

    def tag(self, token: str) -> str:
        return "x"

    def freq(self, token: str) -> int:
        return 1
