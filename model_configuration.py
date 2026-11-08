"""Presentation-only configuration keyed by verified adapter category IDs."""

import hashlib
import json
import re
from pathlib import Path

LEGACY_ZONE_NAMES = json.loads(
    (Path(__file__).resolve().with_name("subbasin_names.json")).read_text(
        encoding="utf-8"
    )
)


PALETTE = [
    "#137f8b",
    "#d35a49",
    "#9472ad",
    "#b48b31",
    "#537db5",
    "#4e9a68",
    "#ae6688",
    "#6d7690",
    "#d07e48",
    "#718b47",
    "#435ba8",
    "#8c564b",
    "#a54370",
    "#467b96",
    "#806930",
    "#396f45",
    "#ad6a1d",
    "#7b4d9c",
    "#a54b3d",
    "#526f70",
    "#337e95",
    "#239d87",
    "#427d61",
    "#7955a1",
    "#a75b99",
    "#698c40",
    "#9c7939",
    "#626a79",
]


def _color(value, context):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError(f"{context}: color must be a six-digit hex color")
    return value


def _label(value, context):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: label must be a nonempty string")
    return value


def resolve_zone_configuration(
    definition=None,
    *,
    zone_ids=None,
    zone_labels=None,
    default_singular="Zone",
    default_plural="Zones",
):
    if not isinstance(definition, dict):
        raise ValueError("zones must be an object")
    if set(definition) - {"singular", "plural", "names"}:
        raise ValueError("zones: only singular, plural, and names are supported")
    singular = _label(
        definition.get("singular", default_singular), "zones.singular"
    ).strip()
    plural = _label(definition.get("plural", default_plural), "zones.plural").strip()
    supplied = definition.get("names", {})
    if not isinstance(supplied, dict):
        raise ValueError("zones.names must be an object keyed by ZoneBudget zone ID")
    configured = {}
    for key, value in supplied.items():
        if not isinstance(key, str) or not re.fullmatch(r"-?(?:0|[1-9]\d*)", key):
            raise ValueError(
                f"zones.names key {key!r} must be an integer ZoneBudget zone ID"
            )
        zone_id = int(key)
        if zone_id == -99:
            raise ValueError(
                "zones.names cannot configure reserved unassigned zone -99"
            )
        configured[key] = _label(value, f"zones.names[{key!r}]").strip()
    if zone_ids is None:
        names = configured
    else:
        valid = {int(zone_id) for zone_id in zone_ids if int(zone_id) != -99}
        unknown = set(configured) - {str(zone_id) for zone_id in valid}
        if unknown:
            raise ValueError(
                f"zones.names contains IDs not declared or assigned by the ZoneBudget definition: {sorted(unknown)}"
            )
        labels = zone_labels or {}
        names = {}
        for zone_id in sorted(valid):
            key = str(zone_id)
            file_label = labels.get(zone_id, labels.get(key))
            if (
                not isinstance(file_label, str)
                or not file_label.strip()
                or file_label.strip() == key
            ):
                file_label = f"{singular} {zone_id}"
            names[key] = configured.get(key, file_label.strip())
        names["-99"] = "Unassigned (ZoneBudget -99)"
    return {"singular": singular, "plural": plural, "names": names}


def apply_legacy_zone_names(manifest):
    """Apply the verified C2VSimFG v1.5 crosswalk to older snapshots only."""
    configuration = manifest.get("model_configuration") or {}
    if "zones" in configuration:
        return manifest
    source = (manifest.get("sources") or {}).get("subbasins") or {}
    if Path(source.get("file", "")).stem.casefold() != "c2vsimfg_zbudget_gwsubbasins":
        return manifest
    names = {}
    for zone_id, label in (manifest.get("subbasins") or {}).items():
        if zone_id == "-99":
            names[zone_id] = "Unassigned (ZoneBudget -99)"
        elif label in LEGACY_ZONE_NAMES:
            names[zone_id] = f"{LEGACY_ZONE_NAMES[label]} ({label})"
        else:
            names[zone_id] = label
    manifest["zone_display"] = {
        "singular": "Subbasin",
        "plural": "Subbasins",
        "names": names,
    }
    return manifest


def legacy_c2_zone_settings(zone_definition, zone_labels):
    if (
        not zone_definition
        or Path(zone_definition).stem.casefold() != "c2vsimfg_zbudget_gwsubbasins"
    ):
        return None
    names = {}
    for zone_id, label in (zone_labels or {}).items():
        if int(zone_id) == -99 or not isinstance(label, str):
            continue
        names[str(zone_id)] = (
            f"{LEGACY_ZONE_NAMES[label]} ({label})"
            if label in LEGACY_ZONE_NAMES
            else label
        )
    return {"singular": "Subbasin", "plural": "Subbasins", "names": names}


def resolve_configuration(
    config, categories, *, zone_ids=None, zone_labels=None, zone_definition=None
):
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Model configuration requires version=1")
    unknown = set(config) - {
        "version",
        "categories",
        "groups",
        "initial_map_category",
        "model_title",
        "export_filename_prefix",
        "zones",
    }
    if unknown:
        raise ValueError(f"Unknown model configuration fields: {sorted(unknown)}")
    definitions = config.get("categories", {})
    if not isinstance(definitions, dict):
        raise ValueError("categories must be an object keyed by category ID")
    known = {category.id for category in categories}
    if set(definitions) - known:
        raise ValueError(f"Unknown category IDs: {sorted(set(definitions) - known)}")
    codes, labels, colors, identity = {}, {}, {}, {}
    for category in categories:
        options = definitions.get(category.id, {})
        if not isinstance(options, dict) or set(options) - {"label", "color"}:
            raise ValueError(
                f"{category.id}: only label and color may be configured; input order is authoritative"
            )
        codes.setdefault(category.source, []).append(category.id)
        labels[category.id] = _label(options.get("label", category.label), category.id)
        fallback = PALETTE[
            int.from_bytes(hashlib.sha256(category.id.encode()).digest()[:4], "little")
            % len(PALETTE)
        ]
        colors[category.id] = _color(options.get("color", fallback), category.id)
        identity[category.id] = {
            "source": category.source,
            "acreage_column": category.acreage_column,
            "code": category.code,
            "legacy_alias": category.legacy_alias,
        }
    definitions = config.get("groups", [])
    if not isinstance(definitions, list):
        raise ValueError("groups must be a list")
    groups = []
    group_ids = set()
    toggles = set()
    for definition in definitions:
        if not isinstance(definition, dict) or set(definition) - {
            "id",
            "label",
            "members",
            "color",
            "chart_toggle",
            "minimum_members",
            "partial_label",
        }:
            raise ValueError("Invalid group definition fields")
        group_id = definition.get("id")
        if (
            not isinstance(group_id, str)
            or not re.fullmatch(r"group:[a-z0-9_-]+", group_id)
            or group_id in group_ids
        ):
            raise ValueError(
                f"Invalid or duplicate group ID: {group_id!r}; use a unique group: identifier"
            )
        members = definition.get("members")
        if (
            not isinstance(members, list)
            or not members
            or any(not isinstance(member, str) for member in members)
        ):
            raise ValueError(
                f"{group_id}: members must be a nonempty list of category IDs"
            )
        if len(set(members)) != len(members) or set(members) - known:
            raise ValueError(
                f"{group_id}: duplicate or unknown category IDs in members: {members}"
            )
        toggle = definition.get("chart_toggle")
        if toggle is not None and (
            toggle not in {"nonpondedgroup", "rice", "refuge", "native"}
            or toggle in toggles
        ):
            raise ValueError(f"{group_id}: invalid or reused chart_toggle: {toggle!r}")
        if toggle:
            toggles.add(toggle)
        minimum = definition.get("minimum_members", 1)
        if type(minimum) is not int or not 1 <= minimum <= len(members):
            raise ValueError(
                f"{group_id}: minimum_members must be between 1 and the group size"
            )
        partial = definition.get("partial_label")
        if partial is not None:
            partial = _label(partial, group_id)
        groups.append(
            {
                "id": group_id,
                "label": _label(definition.get("label"), group_id),
                "members": list(members),
                "color": _color(definition.get("color"), group_id),
                "chart_toggle": toggle,
                "minimum_members": minimum,
                "partial_label": partial,
            }
        )
        group_ids.add(group_id)
    initial = config.get("initial_map_category")
    if not isinstance(initial, str) or initial not in known | group_ids:
        raise ValueError(f"Unknown initial_map_category ID: {initial!r}")
    zone_configuration = config.get("zones")
    if "zones" not in config:
        zone_configuration = legacy_c2_zone_settings(zone_definition, zone_labels)
    if zone_configuration is None:
        zone_configuration = {}
    result = {
        "version": 1,
        "codes": codes,
        "labels": labels,
        "category_colors": colors,
        "category_identity": identity,
        "groups": groups,
        "initial_map_category": initial,
        "zones": resolve_zone_configuration(
            zone_configuration, zone_ids=zone_ids, zone_labels=zone_labels
        ),
    }
    if "model_title" in config:
        result["model_title"] = _label(config["model_title"], "model_title")
    if "export_filename_prefix" in config:
        prefix = config["export_filename_prefix"]
        if (
            not isinstance(prefix, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", prefix)
            or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", prefix, re.I)
        ):
            raise ValueError(
                "export_filename_prefix: use 1-80 ASCII letters/digits/underscores/hyphens, "
                "starting with a letter or digit; paths and reserved device names are not allowed"
            )
        result["export_filename_prefix"] = prefix
    return result


CONFIGURED_JS = r"""
function htmlText(value){return String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]))}
function modelTitle(){return manifest.model_configuration?.model_title||'C2VSimFG land use'}
function exportPrefix(){return manifest.model_configuration?.export_filename_prefix||'C2VSimFG_landuse'}
function hasSubbasins(){return Object.keys(manifest.subbasins||{}).length>0}
function zoneConfiguration(){return manifest.model_configuration?.zones||manifest.zone_display||{singular:'Subbasin',plural:'Subbasins',names:{}}}
function zoneName(id){
    const key=String(id),configuration=manifest.model_configuration?.zones,display=zoneConfiguration(),fileLabel=manifest.subbasins?.[key];
    if(key==='-99')return fileLabel||'Unassigned (ZoneBudget -99)';
    if(configuration?.names?.[key])return configuration.names[key];
    return display.names?.[key]||fileLabel||`${display.singular||'Zone'} ${key}`;
}
function zoneTitle(id){
    const key=String(id),name=zoneName(key),term=zoneConfiguration().singular.toLowerCase(),lower=name.toLowerCase();
    if(key==='-99'||lower.startsWith(term+' ')||lower.endsWith(' '+term))return name;
    return `${zoneConfiguration().singular} ${name}`;
}
function applyZonePresentation(){
    const display=zoneConfiguration(),option=$('mode').querySelector('[value=subbasin]');
    if(!hasSubbasins()){
        option.hidden=true;option.style.display='none';option.disabled=true;
        $('subbasinboundaries').checked=false;$('subbasinboundaries').disabled=true;
        $('subbasinboundaries').closest('label').hidden=true;$('subbasinboundaries').closest('label').style.display='none';$('subbasinreference').hidden=true;$('basincontext').hidden=true;
        return;
    }
    option.hidden=false;option.style.display='';option.disabled=false;option.textContent=display.singular;
    $('zoneBoundaryLabel').textContent=`Show ${display.singular} boundaries`;
    $('zoneReferenceLabel').textContent=display.singular;
    $('zoneJumpPlaceholder').textContent=`Choose a ${display.singular.toLowerCase()}`;
    if($('zoneHelpPlural'))$('zoneHelpPlural').textContent=display.plural.toLowerCase();
    $('subbasinboundaries').disabled=false;$('subbasinboundaries').closest('label').hidden=false;$('subbasinboundaries').closest('label').style.display='';
}
function applyPresentation(){
    const title=manifest.model_configuration?.model_title;
    if(title){document.title=title;document.querySelector('header h1').textContent=title}
}
function configuredGroups(codes){
    const definitions=manifest.model_configuration.groups;
    const selected=definitions.find(group=>group.id===$('metric').value);
    if(selected&&codes.length===selected.members.length&&codes.every(code=>selected.members.includes(code)))
        return [{id:selected.id,name:selected.label,codes:[...selected.members],color:selected.color}];
    const items=[],seen=new Set();
    for(const code of codes){
        if(seen.has(code))continue;
        const definition=definitions.find(group=>group.chart_toggle&&$(group.chart_toggle).checked&&group.members.includes(code)&&group.members.filter(member=>codes.includes(member)&&!seen.has(member)).length>=(group.minimum_members||1));
        if(definition){
            const members=definition.members.filter(member=>codes.includes(member)&&!seen.has(member));
            members.forEach(member=>seen.add(member));
            items.push({id:definition.id,name:members.length<definition.members.length&&definition.partial_label?`${definition.partial_label} (${members.length})`:definition.label,codes:members,color:definition.color});
        }else{seen.add(code);items.push({id:code,name:manifest.labels[code],codes:[code]})}
    }
    return items;
}
"""


def prepare_template(html):
    replacements = {
        "function active(){": CONFIGURED_JS + "\nfunction active(){",
        "function grouped(codes){": "function grouped(codes){if(manifest.model_configuration)return configuredGroups(codes);",
        "function seriesColor(g){": "function seriesColor(g){if(manifest.model_configuration)return g.color||manifest.model_configuration.category_colors[g.id||g.codes[0]];",
        "metric.value='All rice';syncChart();": "metric.value=manifest.model_configuration?manifest.model_configuration.initial_map_category:'All rice';syncChart();",
        "manifest=applySubbasinNames(await api('/api/manifest'));": "manifest=await api('/api/manifest');applyZonePresentation();applyPresentation();",
        "Object.entries(manifest.subbasins||{}).find(([id,name])=>name===raw||manifest.subbasin_ids?.[id]===raw)": "Object.entries(manifest.subbasins||{}).find(([id,name])=>zoneName(id)===raw||name===raw||id===raw||manifest.subbasin_ids?.[id]===raw)",
        "function selectionLabel(){return mode()==='region'?'subregion':mode()==='subbasin'?'subbasin':selectedIds.size>1?'selected elements':'element'}": "function selectionLabel(){return mode()==='region'?'subregion':mode()==='subbasin'?zoneConfiguration().singular.toLowerCase():selectedIds.size>1?'selected elements':'element'}",
        "const names=basins.map(id=>manifest.subbasins?.[id]||'Unassigned');": "const names=hasSubbasins()?basins.map(zoneName):[];",
        "+' · Subbasin'+(info.names.length===1?'':'s')+': '+info.names.join('; ')": "+(hasSubbasins()?' · '+zoneConfiguration().plural+': '+info.names.join('; '):'')",
        "const title=m==='region'?'Subregion '+d.selectionKey:m==='subbasin'?'Subbasin '+(manifest.subbasins[d.selectionKey]||d.selectionKey):elementIds.length===1?'Element '+elementIds[0]:elementIds.length+' selected elements';": "const title=m==='region'?'Subregion '+d.selectionKey:m==='subbasin'?zoneTitle(d.selectionKey):elementIds.length===1?'Element '+elementIds[0]:elementIds.length+' selected elements';",
        "if(!info){$('selectioncontext').textContent='';$('basincontext').textContent='Select a subbasin to see its subregions.';$('subbasinjump').value='';return}": "if(!info){$('selectioncontext').textContent='';$('basincontext').textContent='Select a '+zoneConfiguration().singular.toLowerCase()+' to see its subregions.';$('subbasinjump').value='';return}",
        "$('subbasinjump').value=basinMode?String(selectedData.selectionKey):'';if(basinMode)$('goto').value=manifest.subbasins[selectedData.selectionKey];": "$('subbasinjump').value=basinMode?String(selectedData.selectionKey):'';if(basinMode)$('goto').value=zoneName(selectedData.selectionKey);",
        "Object.values(manifest.subbasins||{}).sort((a,b)=>a.localeCompare(b))": "Object.keys(manifest.subbasins||{}).map(zoneName).sort((a,b)=>a.localeCompare(b))",
        "const label=currentMode==='element'?'element':currentMode==='region'?'subregion':'subbasin';": "const label=currentMode==='element'?'element':currentMode==='region'?'subregion':zoneConfiguration().singular.toLowerCase();",
        "field.placeholder=currentMode==='subbasin'?'Type or choose a subbasin':'Type or choose '+(currentMode==='element'?'an element ID':'a subregion number');": "field.placeholder=currentMode==='subbasin'?'Type or choose a '+zoneConfiguration().singular.toLowerCase():'Type or choose '+(currentMode==='element'?'an element ID':'a subregion number');",
        "manifest.subbasins[a[0]].localeCompare(manifest.subbasins[b[0]])": "zoneName(a[0]).localeCompare(zoneName(b[0]))",
        "manifest.subbasins[id]+' · Subregions '": "zoneName(id)+' · Subregions '",
        "if(!manifest.subbasins?.[g.subbasin])continue;": "if(g.subbasin===null||g.subbasin===undefined)continue;",
        "$('goto').value=manifest.subbasins[$('subbasinjump').value]": "$('goto').value=zoneName($('subbasinjump').value)",
        "(mode()==='subbasin'?'Subbasin '+d.name:'Subregion '+id)": "(mode()==='subbasin'?zoneTitle(id):'Subregion '+id)",
        "mode()==='subbasin'?'Subbasin '+manifest.subbasins[g.subbasin]": "mode()==='subbasin'?zoneTitle(g.subbasin)",
        '<option value="subbasin">Subbasin</option>': '<option value="subbasin">Zone</option>',
        '<label><input id="subbasinboundaries" type="checkbox"> Show subbasin boundaries</label>': '<label><input id="subbasinboundaries" type="checkbox"> <span id="zoneBoundaryLabel">Show zones boundaries</span></label>',
        '<label>Subbasin <select id="subbasinjump"><option value="">Choose a subbasin</option></select></label>': '<label><span id="zoneReferenceLabel">Zone</span> <select id="subbasinjump"><option id="zoneJumpPlaceholder" value="">Choose a zone</option></select></label>',
        "return 'C2VSimFG_landuse_'+info.title": "return exportPrefix()+'_'+info.title",
        "'Subbasins: '+info.names.join('; '),": "...(hasSubbasins()?[zoneConfiguration().plural+': '+info.names.join('; ')]:[]),",
        "ctx.fillText('C2VSimFG land use · '+info.title,margin,44)": "ctx.fillText(modelTitle()+' · '+info.title,margin,44)",
        "${selectionLabel()}": "${htmlText(selectionLabel())}",
    }
    old = "groups['All nonponded crops']=manifest.codes.nonponded||[];groups['Total irrigated agriculture']=[...groups['All nonponded crops'].filter(c=>c!=='ID'),...groups['All rice']];let metric=$('metric');for(const g of ['Total irrigated agriculture','All nonponded crops','All rice','All refuge','Native + riparian vegetation'])metric.add(new Option(g,g));"
    new = (
        "let metric=$('metric');if(manifest.model_configuration){for(const key of Object.keys(groups))delete groups[key];for(const group of manifest.model_configuration.groups){groups[group.id]=group.members;metric.add(new Option(group.label,group.id))}for(const toggle of ['nonpondedgroup','rice','refuge','native']){const group=manifest.model_configuration.groups.find(group=>group.chart_toggle===toggle);$(toggle).disabled=!group;if(group)$(toggle).parentElement.lastChild.textContent=' '+group.label}}else{"
        + old.replace("let metric=$('metric');", "")
        + "} "
    )
    replacements[old] = new
    reference_visibility = "$('subbasinreference').hidden=mode()!=='subbasin';"
    if html.count(reference_visibility) != 2:
        raise ValueError(
            "Unsupported viewer template subbasin-reference visibility markers"
        )
    html = html.replace(
        reference_visibility,
        "$('subbasinreference').hidden=mode()!=='subbasin'||!hasSubbasins();",
    )
    for old, new in replacements.items():
        if html.count(old) != 1:
            raise ValueError(f"Unsupported viewer template marker: {old[:60]}")
        html = html.replace(old, new)
    html = html.replace("[g.name]", "[g.id||g.name]")
    html = html.replace(
        "({name:manifest.labels[code],codes:[code]})",
        "({id:code,name:manifest.labels[code],codes:[code]})",
    )
    html = html.replace("${g.name}", "${htmlText(g.name)}").replace(
        "${item.name}", "${htmlText(item.name)}"
    )
    return html
