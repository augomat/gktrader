/**
 * Define a tool-only plugin entry.
 *
 * Returns an object that OpenClaw will load as a plugin. When registered,
 * the `register` callback iterates over every tool definition, calls
 * `api.registerTool(tool)` for each, and delegates execution to the
 * user-supplied `execute` function.
 */
export function defineToolPlugin(options) {
    const configSchema = options.configSchema;
    const entry = {
        id: options.id,
        name: options.name,
        description: options.description,
        configSchema,
        register(api) {
            const config = (api.pluginConfig ?? {});
            for (const tool of options.tools) {
                const execute = tool.execute;
                api.registerTool({
                    name: tool.name,
                    label: tool.label,
                    description: tool.description,
                    parameters: tool.parameters,
                    async execute(toolCallId, params) {
                        const result = await execute(params, config, {
                            api,
                            toolCallId,
                        });
                        if (result &&
                            typeof result === "object" &&
                            "content" in result) {
                            return result;
                        }
                        if (typeof result === "string") {
                            return {
                                content: [{ type: "text", text: result }],
                                details: {},
                            };
                        }
                        return {
                            content: [
                                {
                                    type: "text",
                                    text: JSON.stringify(result, null, 2),
                                },
                            ],
                            details: { result },
                        };
                    },
                }, tool.optional ? { optional: true } : undefined);
            }
        },
    };
    return entry;
}
//# sourceMappingURL=plugin-sdk.js.map