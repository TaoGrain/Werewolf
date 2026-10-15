"""可由应用层统一映射的领域错误。"""


class GameRuleError(ValueError):
    """表示命令违反确定性游戏规则。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
