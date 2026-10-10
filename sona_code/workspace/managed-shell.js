// V1 hooks preserve the native shell tool's validation, permissions and history.
export default async ({ directory }) => {
  let shell = "";
  const send = async (body) => {
    const response = await fetch(process.env.SONACODE_COMMAND_URL + "/event", {
      method: "POST",
      headers: { "Authorization": "Bearer " + process.env.SONACODE_COMMAND_TOKEN, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(3000),
    });
    if (!response.ok) throw new Error("SonaCode execution manager is unavailable");
  };
  return {
    config: async (config) => {
      const original = config.shell || process.env.SHELL || "";
      // Hook failures must never fall back to executing an unmanaged shell.
      config.shell = process.env.SONACODE_COMMAND_RUNNER;
      const response = await fetch(process.env.SONACODE_COMMAND_URL + "/shell", {
        method: "POST",
        headers: { "Authorization": "Bearer " + process.env.SONACODE_COMMAND_TOKEN, "Content-Type": "application/json" },
        body: JSON.stringify({ shell: original }),
        signal: AbortSignal.timeout(3000),
      });
      if (!response.ok) throw new Error("SonaCode managed shell is unavailable");
      const managed = await response.json();
      shell = managed.shell;
      config.shell = managed.runner;
    },
    "shell.env": async (input, output) => {
      if (!input.sessionID) throw new Error("A managed command requires a session ID");
      output.env.SONACODE_COMMAND_SESSION = input.sessionID;
      output.env.SONACODE_COMMAND_SHELL = shell;
    },
    event: async ({ event }) => {
      if (event.type === "session.error" && event.properties.error?.name === "MessageAbortedError") {
        await send({ session_id: event.properties.sessionID, status: "aborted", directory });
        return;
      }
      if (event.type === "message.part.updated") {
        const part = event.properties.part;
        if (part?.type === "tool" && (part.state?.metadata?.interrupted || String(part.state?.output || "").endsWith("<metadata>\nUser aborted the command\n</metadata>"))) {
          await send({ session_id: part.sessionID, status: "aborted", time: part.state.time?.end, directory });
        }
        return;
      }
      if (event.type !== "session.status") return;
      const { sessionID, status } = event.properties;
      await send({ session_id: sessionID, status: status.type, directory });
    },
  };
};
