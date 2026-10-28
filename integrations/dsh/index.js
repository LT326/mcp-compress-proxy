/**
 * DSH bundle 入口。
 *
 * 这个包真正的内容是 cordis.patch.yml：它插入一条 @deepseek-ai/dsh-mcp-client
 * 行，把 MCP server 包在 mcp-compress-proxy 后面。这个模块只是让 package.json
 * 有一个合法的入口（bundle 校验会读 package.json 的 dsh.bundle.patch），
 * 不在任何插件行里被引用，因此 apply 是空操作。
 */
export const name = 'mcp-compress-proxy'

export function apply() {}

export default { name, apply }
