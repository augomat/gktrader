export type { ToolPluginToolDefinition } from "./plugin-sdk.js";
export { GkTraderClient, GkTraderApiError } from "./client.js";
declare const _default: {
    id: string;
    name: string;
    description: string;
    configSchema: Record<string, unknown>;
    register: (api: {
        pluginConfig: unknown;
        registerTool: Function;
    }) => void;
};
export default _default;
//# sourceMappingURL=index.d.ts.map