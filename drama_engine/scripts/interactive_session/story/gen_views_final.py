"""完整生成 The Clause YAML 的脚本，保留手写格式 + 添加 views。"""

# 23 个有视频的场景及其 node 编号
scenes_with_video = {
    's_01_first_meet': 1,
    's_03_clause': 3,
    's_04_offer_b': 4,
    's_05_offer_a': 5,
    's_06_fitting_b': 6,
    's_07_return': 7,
    's_09_still_on_b': 9,
    's_10_party': 10,
    's_11_photo': 11,
    's_13_pressure_b': 13,
    's_14_leak': 14,
    's_16_argument_b': 16,
    's_17_hospital': 17,
    's_19_secret_b': 19,
    's_20_hood': 20,
    's_21_attack': 21,
    's_23_almost_b': 23,
    's_24_doubts': 24,
    's_25_day30': 25,
    's_27_now_what_b': 27,
    's_28_end_reason': 28,
    's_29_end_heart': 29,
    's_30_end_winner': 30,
}

def generate_video_view_block(scene_id, node_num, title_hint=""):
    """生成一个 video view 块（YAML 格式，6 空格缩进）。"""
    base_url = "https://assets.castloop.ai/xnarrator-game/editor-beta/1780365598"
    return f"""      views:
        - id: {scene_id}_video
          kind: video
          title: {title_hint[:30]}...
          audience: public
          projector: core.views.media
          data:
            url: {base_url}/video/node_{node_num:02d}.mp4
            subtitle_url: {base_url}/subtitles/node_{node_num:02d}.srt
            poster: {base_url}/video/node_{node_num:02d}_poster.jpg
            autoplay: false"""

# 读取当前 yaml_dumped 版本，提取所有 scene 的 publication.messages 作为 title_hint
import yaml
with open('/Volumes/DISK/programs/ccserver/drama_engine/scripts/interactive_session/story/the_clause.yaml.yaml_dumped', 'r', encoding='utf-8') as f:
    dumped = yaml.safe_load(f)

scene_titles = {}
for sid, sc in dumped['scenes'].items():
    msgs = sc.get('publication', {}).get('messages', [])
    if msgs:
        text = msgs[0].get('content', {}).get('text', '')
        scene_titles[sid] = text[:25]

# 输出完整的 scenes 片段（可直接追加到文件）
for scene_id in sorted(scenes_with_video.keys()):
    node_num = scenes_with_video[scene_id]
    title = scene_titles.get(scene_id, "")
    print(f"\n  # {scene_id} (node {node_num}):")
    print(generate_video_view_block(scene_id, node_num, title))
