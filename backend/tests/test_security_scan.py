"""Tests for the deterministic security scan.

Every credential-looking string below is fabricated and format-valid only.

Two properties matter most and are asserted repeatedly:

    1. A real secret is never reproduced in a finding.
    2. A missing best practice is never reported as a vulnerability.
"""

from __future__ import annotations

from app.services.analysis.security_scan import (
    CONFIRMED,
    POTENTIAL,
    SEVERITY_HIGH,
    scan_file,
    scan_files,
)

FAKE_SECRET = "a7Fk29Lm4Xq8Zt6Bv3Nc1Wp5"
FAKE_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def rules(hits) -> set[str]:
    return {hit.rule for hit in hits}


# --- confirmed issues ---------------------------------------------------------


def test_hardcoded_secret_is_confirmed_and_located() -> None:
    content = f'DEBUG_FLAG = 1\nAPI_KEY = "{FAKE_SECRET}"\n'

    hits = scan_file("app/config.py", content)
    hit = next(h for h in hits if h.rule == "hardcoded_secret")

    assert hit.confidence == CONFIRMED
    assert hit.severity == SEVERITY_HIGH
    assert hit.file == "app/config.py"
    assert hit.line == 2  # a real line number
    assert content.splitlines()[hit.line - 1].strip().startswith("API_KEY")


def test_the_secret_value_is_never_reproduced() -> None:
    """The single most important property of this module."""
    report = scan_files(
        {
            "a.py": f'API_KEY = "{FAKE_SECRET}"\n',
            "b.py": f'token = "{FAKE_TOKEN}"\n',
            "c.pem": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n",
        }
    )

    everything = " ".join(
        hit.excerpt + hit.reason + hit.title
        for hit in report.confirmed + report.potential
    )
    assert FAKE_SECRET not in everything
    assert FAKE_TOKEN not in everything
    assert "MIIEowIBAAKCAQEA" not in everything
    assert "[REDACTED]" in everything


def test_redaction_keeps_the_variable_name_visible() -> None:
    """A reviewer needs to know *where* the problem is, not what the value was."""
    hits = scan_file("app/config.py", f'API_KEY = "{FAKE_SECRET}"')

    excerpt = next(h for h in hits if h.rule == "hardcoded_secret").excerpt
    assert "API_KEY" in excerpt
    assert FAKE_SECRET not in excerpt


def test_sql_injection_is_confirmed() -> None:
    hits = scan_file("app/db.py", 'cur.execute(f"SELECT * FROM users WHERE id={uid}")')

    assert "sql_string_building" in rules(hits)
    assert all(h.confidence == CONFIRMED for h in hits if h.rule == "sql_string_building")


def test_overlapping_sql_rules_report_once() -> None:
    """Two patterns describing the same problem must not double-count."""
    hits = scan_file("app/db.py", 'cur.execute(f"SELECT * FROM users WHERE id={uid}")')

    assert len([h for h in hits if h.line == 1]) == 1


def test_command_injection_is_confirmed() -> None:
    hits = scan_file("app/run.py", 'os.system(f"rm -rf {path}")')

    assert "command_injection" in rules(hits)


def test_dynamic_execution_is_confirmed() -> None:
    hits = scan_file("app/x.py", "result = eval(user_input)")

    assert "dynamic_eval" in rules(hits)


def test_cors_wildcard_is_confirmed() -> None:
    hits = scan_file("app/main.py", 'app.add_middleware(CORSMiddleware, allow_origins=["*"])')

    assert "cors_wildcard" in rules(hits)


def test_disabled_tls_verification_is_confirmed() -> None:
    hits = scan_file("app/net.py", "requests.get(url, verify=False)")

    assert "tls_verification_disabled" in rules(hits)


def test_insecure_deserialisation_is_confirmed() -> None:
    hits = scan_file("app/load.py", "data = pickle.loads(payload)")

    assert "insecure_deserialisation" in rules(hits)


# --- potential risks ----------------------------------------------------------


def test_shell_true_is_potential_not_confirmed() -> None:
    """Correctness depends on whether the command contains variable input."""
    hits = scan_file("app/run.py", 'subprocess.run("ls -la", shell=True)')

    hit = next(h for h in hits if h.rule == "shell_true")
    assert hit.confidence == POTENTIAL


def test_secret_in_log_is_potential() -> None:
    hits = scan_file("app/x.py", "logger.info('user token: %s', token)")

    assert next(h for h in hits if h.rule == "secret_in_log").confidence == POTENTIAL


def test_weak_hash_is_potential_because_context_decides() -> None:
    """MD5 is fine for a checksum and wrong for a password."""
    hits = scan_file("app/x.py", "digest = md5(data)")

    assert next(h for h in hits if h.rule == "weak_hash").confidence == POTENTIAL


# --- what must NOT be reported ------------------------------------------------


def test_environment_lookups_are_not_flagged() -> None:
    """Reading a key from the environment is the correct pattern."""
    report = scan_files(
        {
            "app/config.py": (
                'API_KEY = os.getenv("API_KEY")\n'
                'SECRET = os.environ["SECRET"]\n'
                "const token = process.env.GITHUB_TOKEN\n"
            )
        }
    )

    assert report.confirmed == []


def test_parameterised_queries_are_not_flagged() -> None:
    report = scan_files(
        {"app/db.py": 'cur.execute("SELECT * FROM users WHERE id = ?", [uid])\n'}
    )

    assert report.confirmed == []


def test_ordinary_code_produces_nothing() -> None:
    report = scan_files({"app/math.py": "def add(a, b):\n    return a + b\n"})

    assert report.confirmed == []
    assert report.potential == []


def test_a_missing_best_practice_is_not_a_vulnerability() -> None:
    """Feature 4's central rule: absence is recorded as absence."""
    report = scan_files({"app/main.py": "def index():\n    return 'hello'\n"})

    assert report.confirmed == []
    assert len(report.checked_with_no_findings) > 0
    # It appears as "checked, nothing found" - never as an issue.
    assert any("CORS" in item for item in report.checked_with_no_findings)


def test_patterns_inside_comments_are_ignored() -> None:
    report = scan_files({"app/x.py": "# careful: eval(user_input) would be unsafe\n"})

    assert report.confirmed == []


def test_a_credential_inside_a_comment_is_still_reported() -> None:
    """A committed key is leaked whether or not it is commented out."""
    report = scan_files({"app/x.py": f'# old key: API_KEY = "{FAKE_SECRET}"\n'})

    assert "hardcoded_secret" in rules(report.confirmed)
    assert FAKE_SECRET not in " ".join(hit.excerpt for hit in report.confirmed)


# --- report shape -------------------------------------------------------------


def test_findings_are_ordered_by_severity() -> None:
    report = scan_files(
        {
            "a.py": "digest = md5(x)\n",                      # medium, potential
            "b.py": 'cur.execute(f"SELECT {x}")\n',           # high, confirmed
        }
    )

    assert report.confirmed[0].severity == SEVERITY_HIGH


def test_rules_that_fired_are_absent_from_the_no_findings_list() -> None:
    report = scan_files({"a.py": "result = eval(data)\n"})

    assert "dynamic_eval" in rules(report.confirmed)
    assert not any("Dynamic code execution" == item for item in report.checked_with_no_findings)


def test_empty_input_is_safe() -> None:
    assert scan_file("a.py", "") == []
    report = scan_files({})
    assert report.confirmed == []
    assert len(report.checked_with_no_findings) > 0
