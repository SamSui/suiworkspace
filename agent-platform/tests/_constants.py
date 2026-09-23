"""测试共享常量（增量 2 · 2.1）。"""

# 测试用强密钥（>=32 bytes），避免 PyJWT 的 InsecureKeyLengthWarning
TEST_JWT_SECRET = "a" * 40
TEST_API_KEY_SECRET = "b" * 40