"""Docstring prose is not an env: CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 here must not trip."""
import subprocess

ENV = {
    "DISABLE_TELEMETRY": "1",
    "CLAUDE_CODE_GB_DISK_CACHE_WHEN_TELEMETRY_OFF": "1",
    "CLAUDE_CODE_TOTAL_TOKENS_REMINDER": "off",
}


def run(prompt, config_dir):
    seed_gb_cache(config_dir)
    return subprocess.run(["claude", "-p", prompt], env=ENV, check=True)


def seed_gb_cache(config_dir):
    pass
