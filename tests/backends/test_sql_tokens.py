from django.db.backends import sql_tokens
from django.test import SimpleTestCase


class SplitStatementsTests(SimpleTestCase):
    def test_split(self):
        self.assertEqual(
            sql_tokens.split_statements("SELECT 1; SELECT 2;\n"),
            ["SELECT 1;", "SELECT 2;"],
        )

    def test_semicolon_in_string_and_quoted_name(self):
        sql = """INSERT INTO "a;b" VALUES ('x;y', 'it''s;');"""
        self.assertEqual(sql_tokens.split_statements(sql), [sql])

    def test_comments_removed(self):
        sql = "SELECT 1; -- a; b\n/* c; d */ SELECT 2"
        self.assertEqual(sql_tokens.split_statements(sql), ["SELECT 1;", "SELECT 2"])

    def test_dollar_quoted_string(self):
        sql = "SELECT $tag$a;b$tag$; SELECT 2"
        self.assertEqual(
            sql_tokens.split_statements(sql), ["SELECT $tag$a;b$tag$;", "SELECT 2"]
        )

    def test_unterminated_string(self):
        self.assertEqual(sql_tokens.split_statements("SELECT 'a;b"), ["SELECT 'a;b"])

    def test_empty(self):
        self.assertEqual(sql_tokens.split_statements(" ;; \n"), [";", ";"])


class TokenHelpersTests(SimpleTestCase):
    def test_tokenize_roundtrip(self):
        sql = 'SELECT `a`, [b], "c""d" FROM t WHERE x = \'y\' -- z\n/* w */'
        self.assertEqual("".join(t.value for t in sql_tokens.tokenize(sql)), sql)

    def test_name_value(self):
        tokens = list(sql_tokens.significant_tokens('a "b""c" `d` [e] 1 \'f\''))
        self.assertEqual(
            [sql_tokens.name_value(t) for t in tokens],
            ["a", 'b"c', "d", "e", None, None],
        )

    def test_parenthesized_groups(self):
        self.assertEqual(
            sql_tokens.parenthesized_groups("CREATE TABLE t (a (1), b) WITHOUT x"),
            ["a (1), b"],
        )
        self.assertEqual(sql_tokens.parenthesized_groups("CREATE TABLE t"), [])

    def test_format_sql(self):
        self.assertEqual(
            sql_tokens.format_sql("select a from t where a = 'from'"),
            "SELECT a\nFROM t\nWHERE a = 'from'",
        )
