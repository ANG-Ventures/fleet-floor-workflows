const { spawn } = require("child_process");
const env = { ...process.env, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1", CLAUDE_CODE_GB_DISK_CACHE_WHEN_TELEMETRY_OFF: "1" };
// cachedGrowthBookFeatures seeded elsewhere
spawn("claude", ["-p", "hi"], { env });
