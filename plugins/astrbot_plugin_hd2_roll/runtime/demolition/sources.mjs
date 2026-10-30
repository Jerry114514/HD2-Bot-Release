// 拆洞机制的第一手来源与规则表。每个常数都带可回查定位（页面、文件、locator、原文摘录）。
// 未列出的机制一律视为未知；代码不得替任何未知语义默认成功。
// 来源文本为原样摘录，字段语义讨论见 docs/loadout-v3-demolition-report.md。

// 洞口关闭规则唯一第一手来源（本项目暂存资料内）：wiki Missions 页 Stalker Lair 节。
// 该 JSON 只有 updated_at，没有逐页 revid；引用时按 locator 回查，不虚构 revid。
export const HOLE_MECHANISM_SOURCE = Object.freeze({
  page: 'Missions',
  url: 'https://helldivers.wiki.gg/wiki/Missions',
  file: 'HD2_Wiki/data/wiki/zh/missions.json',
  locator: 'categories[id="tactical"].tasks[id="stalker_lair"].tactical_info',
  updated_at: '2026-08-26T20:04:41Z',
  revid: null,
  note: 'missions.json 未携带 revid；按 locator+updated_at 回查。原文同时提供中英双语（tactical_info / tactical_info_zh）。',
});

// 三条关闭路线（原文两 ways + 外部伤害细分）。min_force 是闭式下限；“入洞内”是瞄准/落点条件，
// 引擎事件本身不表达落点，属于场景条件，候选动作必须显式声明 delivery。
export const HOLE_ROUTES = Object.freeze({
  inside_explosive: Object.freeze({
    min_force: 20,
    requires_explosive_type: true,
    placement: 'inside',
    quote_en:
      'Stalker holes can only be destroyed in two ways: Damaging the inside of the bug hole: With a minimum demolition force of 20 , which requires an explosive damage type.',
    quote_zh: '追踪虫洞只能以两种方式摧毁：从虫洞内部造成伤害：需要最低 20 拆毁值，且需要爆炸伤害类型。',
  }),
  inside_any: Object.freeze({
    min_force: 30,
    requires_explosive_type: false,
    placement: 'inside',
    quote_en: 'With a minimum demolition force of 30 , which does not require explosive damage specifically.',
    quote_zh: '需要最低 30 拆毁值，不要求特定爆炸伤害。',
  }),
  outside: Object.freeze({
    min_force: 40,
    requires_explosive_type: false,
    placement: 'outside',
    quote_en: 'Damaging the outside of the bug hole: With a demolition force of 40 or greater.',
    quote_zh: '从虫洞外部造成伤害：需要 40 或更高拆毁值。',
  }),
});

// 普通虫洞适用性：Stalker Lair 的洞与普通 Terminid Nest 的洞同机制，仅尺寸略大（原文明确）。
export const ORDINARY_HOLE_EQUIVALENCE = Object.freeze({
  quote_en:
    'Stalker Lair holes are the same holes that can be found in Terminid Nests , their only difference is a slightly larger bug hole. They can be destroyed the same way.',
  quote_zh: '追踪虫巢穴的洞与终结族巢穴中的洞相同，唯一区别是虫洞稍大。它们可以用相同方式摧毁。',
  source: HOLE_MECHANISM_SOURCE,
});

// 条件性多洞：单枚放置得当的 TED-63 可关两洞。这是落点条件，不是无条件倍增；
// 适配器默认一事件一洞，多洞必须显式引用此条并保持落点条件为字段依赖。
export const MULTI_HOLE_CONDITIONAL = Object.freeze({
  quote_en: 'A single well placed TED-63 Dynamite grenade can destroy 2 holes.',
  quote_zh: '单个放置得当的 TED-63 炸药可以摧毁 2 个洞。',
  source: HOLE_MECHANISM_SOURCE,
  condition: 'well placed（放置得当）',
});

// 载具撞击关洞：出现在钻井任务语境，未给通用阈值/条件，仅作条件路线记录。
export const VEHICLE_RAM_CONDITIONAL = Object.freeze({
  quote_en: 'Bug holes at the drilling locations can be easily closed by driving into them.',
  quote_zh: '钻井地点的虫洞可以通过驾车冲入轻松关闭。',
  source: Object.freeze({...HOLE_MECHANISM_SOURCE, locator: 'Tectonic Drill 任务说明（全文检索命中）'}),
  condition: '仅见于钻井地点语境；通用条件与阈值未知',
});

// 区分“拆迁值机制”与“普通伤害/耐久伤害/AP/破坏力”：
// 1) 洞口只认拆毁值路线（上表），普通伤害数字不在任何路线里；
// 2) 其他结构的拆毁值门槛示例，证明 demolition force 是独立闸门而非伤害换算；
// 3) “几乎无护甲可被持续伤害磨掉”说的是尖啸巢/建筑血量，不是普通洞口，不能混用。
export const STRUCTURE_FORCE_TABLE = Object.freeze([
  Object.freeze({
    structure: 'broadcast tower（广播塔）',
    min_force: 30,
    quote_en:
      'the tower itself can be destroyed with any source of Demolition equal to or higher than 30 to instantly complete the objective. Examples include most grenades , the AC-8 Autocannon , GL-21 Grenade Launcher , and R-36 Eruptor .',
    locator: '类别 tactical/mission 文本（全文检索命中）',
  }),
  Object.freeze({structure: 'shrieker-nest-like tower（尖啸巢级塔）', min_force: 50, quote_en: 'They require 50 demolition force to destroy directly.', locator: '同上'}),
  Object.freeze({structure: 'cooling tower（冷却塔）', min_force: 50, quote_en: 'The Cooling Tower requires a demolition force of 50 to destroy', locator: '同上'}),
  Object.freeze({structure: 'fuel reserves（燃料储备）', min_force: 60, quote_en: 'The Fuel Reserves can only be destroyed with demolition force 60', locator: '同上'}),
  Object.freeze({structure: 'gazer spire（凝视者尖塔）', min_force: 60, quote_en: 'The Gazer Spire requires a demolition force of 60', locator: '同上'}),
]);

export const DAMAGE_IS_NOT_FORCE_QUOTE = Object.freeze({
  quote_en:
    'The Lightning Spires and the conduit-torch towers are considered as structures which require demolition force for the destruction. This means weapons like PLAS-1 Scorcher won\'t be able to destroy these towers.',
  quote_zh: '闪电尖塔和导管火把塔被视为需要拆毁值才能摧毁的结构。这意味着 PLAS-1 焦土等武器无法摧毁这些塔。',
  point: '高伤害/高AP不等于拆毁值；拆毁值是独立闸门。',
});

// 逐装备来源：equipment-expanded.json 的攻击表。demolition_force 行在 AoE Effect 节，
// 与 Damage（damage_inner_radius/inner_durable…）、Penetration（pen_inner…）分列。
export const EQUIPMENT_ARCHIVE = Object.freeze({
  file: 'scripts/.loadout-inputs/equipment-expanded.json',
  row_key: 'demolition_force',
  row_section: 'AoE Effect',
  provenance_fields: Object.freeze(['source_url', 'revid', 'revision_timestamp', 'fetched_at', 'html_sha256']),
});

// 玩家口径（2026-10-24 用户确认；只记录，不数值化）：公共补给每次只计本人 1 盒；
// 逐装备恢复量取来源行（mags_from_supply 等），缺失即依赖，不默认补满；地图拾取不保证。
export const RESUPPLY_POLICY = Object.freeze({
  boxes_per_call_per_player: 1,
  rule: '公共 Resupply 每次呼叫只计单人 1 盒，其余 3 盒不计为个人可用资源',
  per_equipment_source_rows: Object.freeze(['mags_from_supply', 'mags_from_ammo_box']),
  no_default_full_refill: true,
  map_pickup_assumed: false,
});

// 引擎现状（只读核对，不改）：damage.mjs demolitionRoute 只接受 kind==='explosion' 且
// demolition>=20，或 id 恰为 'southwest' 的已核验路线。因此：
//  - inside_explosive（≥20 爆炸）与引擎主路径语义一致；
//  - inside_any（≥30 非爆炸直击/光束/电弧）与 outside（≥40）无法用其他 route id 认证；
//  - 事件不表达落点（inside/outside），属于场景条件，由候选动作的 delivery 字段显式声明。
export const ENGINE_ROUTE_CONSTRAINTS = Object.freeze({
  explosion_min_force: 20,
  only_route_id: 'southwest',
  file: 'shared/hd2-loadout/engine/damage.mjs',
  gap_id: 'DEM-ENGINE-1',
});
