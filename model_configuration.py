"""Presentation-only configuration keyed by verified adapter category IDs."""

import hashlib
import re


PALETTE = ['#137f8b', '#d35a49', '#9472ad', '#b48b31', '#537db5', '#4e9a68',
           '#ae6688', '#6d7690', '#d07e48', '#718b47', '#435ba8', '#8c564b',
           '#a54370', '#467b96', '#806930', '#396f45', '#ad6a1d', '#7b4d9c',
           '#a54b3d', '#526f70', '#337e95', '#239d87', '#427d61', '#7955a1',
           '#a75b99', '#698c40', '#9c7939', '#626a79']


def _color(value, context):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValueError(f'{context}: color must be a six-digit hex color')
    return value


def _label(value, context):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{context}: label must be a nonempty string')
    return value


def resolve_configuration(config, categories):
    if not isinstance(config, dict) or config.get('version') != 1:
        raise ValueError('Model configuration requires version=1')
    unknown = set(config) - {'version', 'categories', 'groups', 'initial_map_category', 'model_title', 'export_filename_prefix'}
    if unknown:
        raise ValueError(f'Unknown model configuration fields: {sorted(unknown)}')
    definitions = config.get('categories', {})
    if not isinstance(definitions, dict):
        raise ValueError('categories must be an object keyed by category ID')
    known = {category.id for category in categories}
    if set(definitions) - known:
        raise ValueError(f'Unknown category IDs: {sorted(set(definitions) - known)}')
    codes, labels, colors, identity = {}, {}, {}, {}
    for category in categories:
        options = definitions.get(category.id, {})
        if not isinstance(options, dict) or set(options) - {'label', 'color'}:
            raise ValueError(f'{category.id}: only label and color may be configured; input order is authoritative')
        codes.setdefault(category.source, []).append(category.id)
        labels[category.id] = _label(options.get('label', category.label), category.id)
        fallback = PALETTE[int.from_bytes(hashlib.sha256(category.id.encode()).digest()[:4], 'little') % len(PALETTE)]
        colors[category.id] = _color(options.get('color', fallback), category.id)
        identity[category.id] = {'source': category.source, 'acreage_column': category.acreage_column,
                                 'code': category.code, 'legacy_alias': category.legacy_alias}
    definitions = config.get('groups', [])
    if not isinstance(definitions, list):
        raise ValueError('groups must be a list')
    groups = []
    group_ids = set()
    toggles = set()
    for definition in definitions:
        if not isinstance(definition, dict) or set(definition) - {'id', 'label', 'members', 'color', 'chart_toggle', 'minimum_members', 'partial_label'}:
            raise ValueError('Invalid group definition fields')
        group_id = definition.get('id')
        if not isinstance(group_id, str) or not re.fullmatch(r'group:[a-z0-9_-]+', group_id) or group_id in group_ids:
            raise ValueError(f'Invalid or duplicate group ID: {group_id!r}; use a unique group: identifier')
        members = definition.get('members')
        if not isinstance(members, list) or not members or any(not isinstance(member, str) for member in members):
            raise ValueError(f'{group_id}: members must be a nonempty list of category IDs')
        if len(set(members)) != len(members) or set(members) - known:
            raise ValueError(f'{group_id}: duplicate or unknown category IDs in members: {members}')
        toggle = definition.get('chart_toggle')
        if toggle is not None and (toggle not in {'nonpondedgroup', 'rice', 'refuge', 'native'} or toggle in toggles):
            raise ValueError(f'{group_id}: invalid or reused chart_toggle: {toggle!r}')
        if toggle:
            toggles.add(toggle)
        minimum = definition.get('minimum_members', 1)
        if type(minimum) is not int or not 1 <= minimum <= len(members):
            raise ValueError(f'{group_id}: minimum_members must be between 1 and the group size')
        partial = definition.get('partial_label')
        if partial is not None:
            partial = _label(partial, group_id)
        groups.append({'id': group_id, 'label': _label(definition.get('label'), group_id),
                       'members': list(members), 'color': _color(definition.get('color'), group_id),
                       'chart_toggle': toggle, 'minimum_members': minimum, 'partial_label': partial})
        group_ids.add(group_id)
    initial = config.get('initial_map_category')
    if not isinstance(initial, str) or initial not in known | group_ids:
        raise ValueError(f'Unknown initial_map_category ID: {initial!r}')
    result = {'version': 1, 'codes': codes, 'labels': labels, 'category_colors': colors,
              'category_identity': identity, 'groups': groups, 'initial_map_category': initial}
    if 'model_title' in config:
        result['model_title'] = _label(config['model_title'], 'model_title')
    if 'export_filename_prefix' in config:
        prefix = config['export_filename_prefix']
        if (not isinstance(prefix, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', prefix)
                or re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]', prefix, re.I)):
            raise ValueError('export_filename_prefix: use 1-80 ASCII letters/digits/underscores/hyphens, '
                             'starting with a letter or digit; paths and reserved device names are not allowed')
        result['export_filename_prefix'] = prefix
    return result


CONFIGURED_JS = r'''
function htmlText(value){return String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]))}
function modelTitle(){return manifest.model_configuration?.model_title||'C2VSimFG land use'}
function exportPrefix(){return manifest.model_configuration?.export_filename_prefix||'C2VSimFG_landuse'}
function hasSubbasins(){return Object.keys(manifest.subbasins||{}).length>0}
function applyPresentation(){
    const title=manifest.model_configuration?.model_title;
    if(title){document.title=title;document.querySelector('header h1').textContent=title}
    if(!hasSubbasins()){
        const option=$('mode').querySelector('[value=subbasin]');option.hidden=true;option.style.display='none';option.disabled=true;
        $('subbasinboundaries').checked=false;$('subbasinboundaries').disabled=true;
        $('subbasinboundaries').closest('label').hidden=true;$('subbasinboundaries').closest('label').style.display='none';
        $('subbasinreference').hidden=true;$('basincontext').hidden=true;
    }
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
'''


def prepare_template(html):
        replacements = {
                'function active(){': CONFIGURED_JS + '\nfunction active(){',
                'function grouped(codes){': 'function grouped(codes){if(manifest.model_configuration)return configuredGroups(codes);',
                'function seriesColor(g){': 'function seriesColor(g){if(manifest.model_configuration)return g.color||manifest.model_configuration.category_colors[g.id||g.codes[0]];',
                "metric.value='All rice';syncChart();": "metric.value=manifest.model_configuration?manifest.model_configuration.initial_map_category:'All rice';syncChart();",
                "manifest=applySubbasinNames(await api('/api/manifest'));": "manifest=applySubbasinNames(await api('/api/manifest'));applyPresentation();",
                "const names=basins.map(id=>manifest.subbasins?.[id]||'Unassigned');": "const names=hasSubbasins()?basins.map(id=>manifest.subbasins?.[id]||'Unassigned'):[];",
                "+' · Subbasin'+(info.names.length===1?'':'s')+': '+info.names.join('; ')": "+(hasSubbasins()?' · Subbasin'+(info.names.length===1?'':'s')+': '+info.names.join('; '):'')",
                "return 'C2VSimFG_landuse_'+info.title": "return exportPrefix()+'_'+info.title",
                "'Subbasins: '+info.names.join('; '),": "...(hasSubbasins()?['Subbasins: '+info.names.join('; ')]:[]),",
                "ctx.fillText('C2VSimFG land use · '+info.title,margin,44)": "ctx.fillText(modelTitle()+' · '+info.title,margin,44)",
        }
        old = "groups['All nonponded crops']=manifest.codes.nonponded||[];groups['Total irrigated agriculture']=[...groups['All nonponded crops'].filter(c=>c!=='ID'),...groups['All rice']];let metric=$('metric');for(const g of ['Total irrigated agriculture','All nonponded crops','All rice','All refuge','Native + riparian vegetation'])metric.add(new Option(g,g));"
        new = "let metric=$('metric');if(manifest.model_configuration){for(const key of Object.keys(groups))delete groups[key];for(const group of manifest.model_configuration.groups){groups[group.id]=group.members;metric.add(new Option(group.label,group.id))}for(const toggle of ['nonpondedgroup','rice','refuge','native']){const group=manifest.model_configuration.groups.find(group=>group.chart_toggle===toggle);$(toggle).disabled=!group;if(group)$(toggle).parentElement.lastChild.textContent=' '+group.label}}else{" + old.replace("let metric=$('metric');", '') + '} '
        replacements[old] = new
        for old, new in replacements.items():
                if html.count(old) != 1:
                        raise ValueError(f'Unsupported viewer template marker: {old[:60]}')
                html = html.replace(old, new)
        html = html.replace('[g.name]', '[g.id||g.name]')
        html = html.replace('({name:manifest.labels[code],codes:[code]})', '({id:code,name:manifest.labels[code],codes:[code]})')
        html = html.replace('${g.name}', '${htmlText(g.name)}').replace('${item.name}', '${htmlText(item.name)}')
        return html