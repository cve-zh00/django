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

    def test_trigger_body_not_split(self):
        sql = "CREATE TRIGGER t AFTER INSERT ON a BEGIN UPDATE b SET x = 1; END;"
        self.assertEqual(
            sql_tokens.split_statements(f"{sql} SELECT 1;"), [sql, "SELECT 1;"]
        )

    def test_nested_blocks(self):
        sql = (
            "CREATE FUNCTION f() BEGIN IF a THEN SELECT 1; END IF; "
            "CASE WHEN a THEN SELECT 2; END CASE; WHILE b DO SELECT 3; "
            "END WHILE; END;"
        )
        self.assertEqual(
            sql_tokens.split_statements(f"{sql} SELECT 4;"), [sql, "SELECT 4;"]
        )

    def test_declare_block(self):
        sql = "CREATE PROCEDURE p() DECLARE x int; BEGIN SELECT 1; END;"
        self.assertEqual(sql_tokens.split_statements(sql), [sql])

    def test_begin_transaction_is_not_a_block(self):
        for begin in ["BEGIN;", "BEGIN TRANSACTION;", "BEGIN IMMEDIATE;"]:
            with self.subTest(begin=begin):
                self.assertEqual(
                    sql_tokens.split_statements(f"{begin} SELECT 1; COMMIT;"),
                    [begin, "SELECT 1;", "COMMIT;"],
                )

    def test_hash_comment(self):
        self.assertEqual(
            sql_tokens.split_statements("SELECT 1; # a; b\nSELECT 2;"),
            ["SELECT 1;", "SELECT 2;"],
        )

    def test_hash_operator_is_not_a_comment(self):
        sql = "SELECT 1 #2; SELECT 3"
        self.assertEqual(sql_tokens.split_statements(sql), ["SELECT 1 #2;", "SELECT 3"])

    def test_backslash_escaped_quote(self):
        sql = "SELECT 'it\\'s;ok'; SELECT 2;"
        self.assertEqual(
            sql_tokens.split_statements(sql), ["SELECT 'it\\'s;ok';", "SELECT 2;"]
        )

    def test_trailing_backslash_in_string(self):
        self.assertEqual(
            sql_tokens.split_statements("SELECT 'C:\\'; SELECT 2;"),
            ["SELECT 'C:\\';", "SELECT 2;"],
        )

    def test_backslash_in_quoted_name(self):
        sql = 'CREATE TABLE "a\\" ("b" int); SELECT 1;'
        self.assertEqual(
            sql_tokens.split_statements(sql),
            ['CREATE TABLE "a\\" ("b" int);', "SELECT 1;"],
        )

    def test_hostile_input_is_fast(self):
        for sql in ["'" + "a" * 200_000, "$a$ " * 50_000, "[" * 100_000, "(" * 50_000]:
            with self.subTest(sql=sql[:5]):
                sql_tokens.split_statements(sql)

    def test_delimiter_command(self):
        sql = (
            "DELIMITER //\nCREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END//\n"
            "DELIMITER ;\nSELECT 3;"
        )
        self.assertEqual(
            sql_tokens.split_statements(sql),
            ["CREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END", "SELECT 3;"],
        )

    def test_delimiter_command_dollar_signs(self):
        sql = "DELIMITER $$\nSELECT 'a;b'$$\nSELECT 2 -- c$$\n$$\nDELIMITER ;\nSELECT 3"
        self.assertEqual(
            sql_tokens.split_statements(sql), ["SELECT 'a;b'", "SELECT 2", "SELECT 3"]
        )

    def test_delimiter_as_identifier(self):
        sql = "CREATE TABLE t (\n  delimiter text,\n  x int\n); SELECT delimiter FROM t"
        self.assertEqual(
            sql_tokens.split_statements(sql),
            [
                "CREATE TABLE t (\n  delimiter text,\n  x int\n);",
                "SELECT delimiter FROM t",
            ],
        )

    def test_go_batch_separator(self):
        self.assertEqual(
            sql_tokens.split_statements(
                "SELECT 1\nGO\nSELECT 2\n  go 5 -- x\nSELECT 3"
            ),
            ["SELECT 1", "SELECT 2", "SELECT 3"],
        )

    def test_go_not_a_separator(self):
        for sql in [
            "SELECT go FROM t; SELECT 2",
            "SELECT (\n go\n) FROM t",
            "SELECT 'a\nGO\nb'",
            "/* a\nGO\n*/ SELECT 1",
        ]:
            with self.subTest(sql=sql):
                self.assertEqual(
                    sql_tokens.split_statements(sql),
                    sql_tokens._split_on_semicolons(sql),
                )

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
