"""生成 The Clause 完整 scenes（含 views）的辅助脚本。"""
import yaml

# 从 yaml_dumped 版本读取 views 数据
with open('drama_engine/scripts/interactive_session/story/the_clause.yaml.yaml_dumped', encoding='utf-8') as f:
    dumped = yaml.safe_load(f)

# 提取每个 scene 的 views
scene_views = {}
for scene_id, scene in dumped['scenes'].items():
    views = scene.get('publication', {}).get('views', [])
    if views:
        scene_views[scene_id] = views

# 生成 video view 的 YAML 文本
def format_view(view_data, indent=8):
    """格式化一个 view 为缩进的 YAML。"""
    ind = ' ' * indent
    lines = [
        f"{ind}- id: {view_data['id']}",
        f"{ind}  kind: {view_data['kind']}",
        f"{ind}  title: {view_data.get('title', '')}",
        f"{ind}  audience: {view_data.get('audience', 'public')}",
        f"{ind}  projector: {view_data.get('projector', 'core.views.media')}",
        f"{ind}  data:",
        f"{ind}    url: {view_data['data']['url']}",
    ]
    if view_data['data'].get('subtitle_url'):
        lines.append(f"{ind}    subtitle_url: {view_data['data']['subtitle_url']}")
    if view_data['data'].get('poster'):
        lines.append(f"{ind}    poster: {view_data['data']['poster']}")
    lines.append(f"{ind}    autoplay: {str(view_data['data'].get('autoplay', False)).lower()}")
    return '\n'.join(lines)

# 输出每个有 views 的 scene 的 views 块
print("# 以下是每个 scene 需要添加的 views 块（publication.views）：\n")
for scene_id in sorted(scene_views.keys()):
    print(f"# {scene_id}:")
    print("      views:")
    for view in scene_views[scene_id]:
        print(format_view(view))
    print()
