import subprocess
env = {"DISABLE_TELEMETRY": "1", "CLAUDE_CODE_GB_DISK_CACHE_WHEN_TELEMETRY_OFF": "1",
       "CLAUDE_CODE_TOTAL_TOKENS_REMINDER": "off", "CLAUDE_CONFIG_DIR": "/tmp/fresh"}
subprocess.run(["claude", "-p", "hi"], env=env)
