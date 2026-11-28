from spacestudio.permissions import is_valid_rule, summarize


def test_suggests_rules_only_for_blocked_words():
    d = [{"tool_name": "Bash", "tool_input": {"command": "cd /x && curl -L -o a.jpg https://gibs.earthdata.nasa.gov/x | tee log"}},
         {"tool_name": "PowerShell", "tool_input": {"command": "Invoke-WebRequest https://x"}},
         {"tool_name": "WebFetch", "tool_input": {"url": "https://fonts.googleapis.com/css2?family=Noto"}}]
    out = summarize(d, ["Bash(cd *)", "Bash(echo *)"])
    bash = next(o for o in out if o["tool"] == "Bash")
    assert bash["rules"] == ["Bash(curl *)", "Bash(tee *)"]
    assert next(o for o in out if o["tool"] == "PowerShell")["rules"] == []
    assert next(o for o in out if o["tool"] == "WebFetch")["rules"] == ["WebFetch(domain:fonts.googleapis.com)"]


def test_blanket_rules_are_refused():
    for bad in ("Bash", "Bash(*)", "Read(*)", "Bash(rm -rf /)\nWrite", ""):
        assert not is_valid_rule(bad), bad
    for good in ("Bash(curl *)", "Bash(wget *)", "WebFetch(domain:example.com)", "WebSearch"):
        assert is_valid_rule(good), good
