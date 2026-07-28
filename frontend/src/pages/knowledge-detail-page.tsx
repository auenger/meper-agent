/**
 * KnowledgeDetailPage — KB detail (route /knowledge/:id).
 *
 * Loads the KB by id, then renders KbTreeDetail (tree) or KbVectorDetail
 * (vector). Header has a back button + breadcrumb + type-specific subtitle.
 *
 * Layout mirrors frontend-studio's detail page structure.
 */
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Button, Spin, Tag, Result } from 'antd'
import { ArrowLeftOutlined, BookOutlined, SearchOutlined } from '@ant-design/icons'
import { knowledgeApi, knowledgeKeys, type KbType } from '../services/knowledge-api'
import KbTreeDetail from '../features/knowledge_base/KbTreeDetail'
import KbVectorDetail from '../features/knowledge_base/KbVectorDetail'

export default function KnowledgeDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const { data: kb, isLoading, isError } = useQuery({
    queryKey: knowledgeKeys.detail(id ?? ''),
    queryFn: () => knowledgeApi.get(id!),
    enabled: !!id,
  })

  if (isLoading) {
    return <div className="flex justify-center py-20"><Spin size="large" /></div>
  }

  if (isError || !kb) {
    return (
      <Result
        status="404"
        title="知识库不存在"
        subTitle={`找不到 id 为 ${id} 的知识库`}
        extra={<Button type="primary" onClick={() => navigate('/knowledge')}>返回列表</Button>}
      />
    )
  }

  const type = (kb.type === 'vector' ? 'vector' : 'tree') as KbType

  return (
    <div className="animate-[fadeIn_0.3s_ease-out]">
      {/* Header: back + breadcrumb */}
      <div className="flex items-center gap-3 mb-5">
        <Button
          type="text"
          shape="circle"
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate('/knowledge')}
        />
        <div>
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <span>知识库</span>
            <span>/</span>
            <span className="font-semibold text-gray-900">{kb.name}</span>
            {type === 'vector' ? (
              <Tag color="green" icon={<SearchOutlined />} style={{ marginInlineEnd: 0 }}>向量库</Tag>
            ) : (
              <Tag color="blue" icon={<BookOutlined />} style={{ marginInlineEnd: 0 }}>文档树</Tag>
            )}
          </div>
          <p className="text-xs text-gray-400 mt-0.5">
            {type === 'vector'
              ? '上传文档自动解析→切片→向量化；Agent/工作流 可用 kb_search 检索'
              : '绑定到 Agent 后，可用 kb_glob / kb_grep / kb_read 探索'}
          </p>
        </div>
      </div>

      {/* Body */}
      {type === 'vector' ? <KbVectorDetail kb={kb} /> : <KbTreeDetail kb={kb} />}
    </div>
  )
}
