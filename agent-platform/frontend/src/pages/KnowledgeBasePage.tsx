/**
 * 知识库列表（ADR §5.3）：新建 / 搜索 / 编辑 / 软删。
 * 依赖 GET/POST/PATCH/DELETE /v1/kb。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { apiCreateKb, apiDeleteKb, apiListKb, apiUpdateKb } from '../api/kb'
import { SkeletonRows } from '../components/ui/Skeleton'
import { EmptyState, ErrorState } from '../components/ui/Feedback'
import { Spinner } from '../components/ui/Spinner'

export default function KnowledgeBasePage() {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [editId, setEditId] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [search, setSearch] = useState('')

  const { data: kbs, isLoading, isError, error } = useQuery({
    queryKey: ['kb'],
    queryFn: apiListKb,
  })

  const createMut = useMutation({
    mutationFn: () => apiCreateKb(name.trim()),
    onSuccess: () => {
      setName('')
      void qc.invalidateQueries({ queryKey: ['kb'] })
    },
  })
  const updateMut = useMutation({
    mutationFn: ({ id, n }: { id: number; n: string }) => apiUpdateKb(id, n),
    onSuccess: () => {
      setEditId(null)
      void qc.invalidateQueries({ queryKey: ['kb'] })
    },
  })
  const deleteMut = useMutation({
    mutationFn: apiDeleteKb,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['kb'] }),
  })

  const filtered = kbs?.filter((kb) => kb.name.toLowerCase().includes(search.toLowerCase()))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">知识库 RAG</h1>
        <div className="ml-auto flex gap-2">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索知识库…"
            className="rounded ctrl border border-gray-300 px-3 py-1.5 text-sm"
          />
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (name.trim()) createMut.mutate()
            }}
            className="flex gap-2"
          >
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="新建知识库名称"
              className="rounded ctrl border border-gray-300 px-3 py-1.5 text-sm"
            />
            <button
              type="submit"
              disabled={createMut.isPending || !name.trim()}
              className="flex items-center gap-1 rounded ctrl bg-primary px-4 py-1.5 text-sm text-white hover:bg-primary-hover disabled:opacity-60"
            >
              {createMut.isPending && <Spinner size="sm" className="border-white" />}
              新建
            </button>
          </form>
        </div>
      </div>

      {createMut.isError && (
        <ErrorState message={createMut.error instanceof Error ? createMut.error.message : '创建失败'} />
      )}

      {isLoading ? (
        <SkeletonRows rows={4} />
      ) : isError ? (
        <ErrorState message={error instanceof Error ? error.message : '加载失败'} />
      ) : filtered && filtered.length > 0 ? (
        <table className="w-full rounded-card bg-white shadow-card">
          <thead>
            <tr className="border-b border-gray-100 text-left text-xs text-gray-500">
              <th className="px-4 py-3 font-medium">名称</th>
              <th className="px-4 py-3 font-medium">ID</th>
              <th className="px-4 py-3 font-medium text-right">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {filtered.map((kb) => (
              <tr key={kb.id} className="hover:bg-gray-50">
                <td className="px-4 py-3">
                  {editId === kb.id ? (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault()
                        if (editName.trim()) updateMut.mutate({ id: kb.id, n: editName.trim() })
                      }}
                      className="flex items-center gap-2"
                    >
                      <input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="rounded ctrl border border-gray-300 px-2 py-1 text-sm"
                      />
                      <button type="submit" className="text-xs text-primary hover:underline">保存</button>
                      <button type="button" onClick={() => setEditId(null)} className="text-xs text-gray-400 hover:underline">取消</button>
                    </form>
                  ) : (
                    <Link to={`/kb/${kb.id}`} className="font-medium text-gray-800 hover:text-primary">
                      {kb.name}
                    </Link>
                  )}
                </td>
                <td className="px-4 py-3 text-sm text-gray-400">{kb.id}</td>
                <td className="px-4 py-3 text-right">
                  <Link
                    to={`/kb/${kb.id}`}
                    className="rounded ctrl px-2 py-1 text-xs text-primary hover:bg-primary-light"
                  >
                    查看
                  </Link>
                  <button
                    onClick={() => {
                      setEditId(kb.id)
                      setEditName(kb.name)
                    }}
                    className="ml-1 rounded ctrl px-2 py-1 text-xs text-gray-500 hover:bg-gray-100"
                  >
                    编辑
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(`确定删除知识库「${kb.name}」？此操作不可恢复。`)) deleteMut.mutate(kb.id)
                    }}
                    className="ml-1 rounded ctrl px-2 py-1 text-xs text-danger hover:bg-red-50"
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <EmptyState
          title="暂无知识库"
          hint="创建一个知识库，然后上传文档开始 RAG"
        />
      )}
    </div>
  )
}
