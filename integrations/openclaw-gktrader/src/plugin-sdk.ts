/**
 * Local compatibility stub for OpenClaw plugin SDK.
 *
 * This module provides the same `defineToolPlugin` interface that
 * would come from `openclaw/plugin-sdk/tool-plugin`, without depending
 * on the OpenClaw monorepo at build time.
 *
 * When loaded into a running OpenClaw process, the plugin's default
 * export conforms to OpenClaw's `DefinedToolPluginEntry` contract.
 */
import type { TSchema, Static } from "typebox";

export type { TSchema, Static };

// ---- Tool plugin types ---------------------------------------------------

export type ToolPluginExecutionContext = {
  api: unknown;
  signal?: AbortSignal;
  toolCallId: string;
  onUpdate?: unknown;
};

export type ToolPluginToolDefinition<
  TConfig,
  TParamsSchema extends TSchema,
> = {
  name: string;
  label?: string;
  description: string;
  parameters: TParamsSchema;
  optional?: boolean;
  execute: (
    params: Static<TParamsSchema>,
    config: TConfig,
    context: ToolPluginExecutionContext,
  ) => Promise<unknown>;
};

/** Opaque tool entry used in the tools array. */
export type ToolDefinition = {
  name: string;
  label: string;
  description: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  parameters: any;
  optional: boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  execute: (params: any, config: any, context: any) => Promise<unknown>;
};

// ---- defineToolPlugin ----------------------------------------------------

type ToolPluginOptions = {
  id: string;
  name: string;
  description: string;
  configSchema: TSchema;
  tools: readonly ToolDefinition[];
};

type PluginEntry = {
  id: string;
  name: string;
  description: string;
  configSchema: Record<string, unknown>;
  register: (api: { pluginConfig: unknown; registerTool: Function }) => void;
};

/**
 * Define a tool-only plugin entry.
 *
 * Returns an object that OpenClaw will load as a plugin. When registered,
 * the `register` callback iterates over every tool definition, calls
 * `api.registerTool(tool)` for each, and delegates execution to the
 * user-supplied `execute` function.
 */
export function defineToolPlugin(options: ToolPluginOptions): PluginEntry {
  const configSchema = options.configSchema as Record<string, unknown>;

  const entry: PluginEntry = {
    id: options.id,
    name: options.name,
    description: options.description,
    configSchema,

    register(api) {
      const config = (api.pluginConfig ?? {}) as Record<string, unknown>;
      for (const tool of options.tools) {
        const execute = tool.execute;
        api.registerTool(
          {
            name: tool.name,
            label: tool.label,
            description: tool.description,
            parameters: tool.parameters,
            async execute(toolCallId: string, params: unknown) {
              const result = await execute(params, config, {
                api,
                toolCallId,
              });
              if (
                result &&
                typeof result === "object" &&
                "content" in (result as Record<string, unknown>)
              ) {
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
          },
          tool.optional ? { optional: true } : undefined,
        );
      }
    },
  };

  return entry;
}
