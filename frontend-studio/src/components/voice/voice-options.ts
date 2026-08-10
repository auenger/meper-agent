export type VoiceCategory = 'general' | 'dubbing' | 'roleplay'

export interface VoiceOption {
  id: string
  name: string
  gender: '女声' | '男声'
  category: VoiceCategory
  categoryLabel: string
  languages: string
  description: string
}

export const VOICE_OPTIONS: VoiceOption[] = [
  {
    id: 'zh_female_vv_uranus_bigtts',
    name: 'Vivi 2.0',
    gender: '女声',
    category: 'general',
    categoryLabel: '通用',
    languages: '中文 / 英文',
    description: '自然通用，适合日常助手对话',
  },
  {
    id: 'zh_male_dayi_saturn_bigtts',
    name: '大壹',
    gender: '男声',
    category: 'dubbing',
    categoryLabel: '视频配音',
    languages: '中文',
    description: '沉稳男声，适合讲解与播报',
  },
  {
    id: 'zh_female_mizai_saturn_bigtts',
    name: '黑猫侦探社咪仔',
    gender: '女声',
    category: 'dubbing',
    categoryLabel: '视频配音',
    languages: '中文',
    description: '鲜明活泼，适合剧情与内容配音',
  },
  {
    id: 'zh_female_jitangnv_saturn_bigtts',
    name: '鸡汤女',
    gender: '女声',
    category: 'dubbing',
    categoryLabel: '视频配音',
    languages: '中文',
    description: '亲和舒缓，适合情感类内容',
  },
  {
    id: 'zh_female_meilinvyou_saturn_bigtts',
    name: '魅力女友',
    gender: '女声',
    category: 'dubbing',
    categoryLabel: '视频配音',
    languages: '中文',
    description: '亲切自然，适合陪伴式对话',
  },
  {
    id: 'zh_female_santongyongns_saturn_bigtts',
    name: '流畅女声',
    gender: '女声',
    category: 'dubbing',
    categoryLabel: '视频配音',
    languages: '中文',
    description: '清晰流畅，适合信息播报',
  },
  {
    id: 'zh_male_ruyayichen_saturn_bigtts',
    name: '儒雅逸辰',
    gender: '男声',
    category: 'dubbing',
    categoryLabel: '视频配音',
    languages: '中文',
    description: '温和沉稳，适合知识讲解',
  },
  {
    id: 'ICL_zh_female_keainvsheng_tob',
    name: '可爱女生',
    gender: '女声',
    category: 'roleplay',
    categoryLabel: '角色扮演',
    languages: '中文',
    description: '轻快可爱，适合活泼角色',
  },
  {
    id: 'ICL_zh_female_tiaopigongzhu_tob',
    name: '调皮公主',
    gender: '女声',
    category: 'roleplay',
    categoryLabel: '角色扮演',
    languages: '中文',
    description: '俏皮有表现力，适合角色对话',
  },
]
