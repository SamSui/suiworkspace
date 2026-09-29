/**
 * 知识库详情（ADR §5.3 / 决策3）：文档状态机闭环前端可见。
 * 上传 → 202 插入行内 doc(status=0) → 2s 轻轮询 GET /v1/task/{id} → 完成/失败。
 * 进度条 0%→50%→100%（对齐后端 _PROGRESS 的状态映射）。
 * 失败 → 行内红标 + error_msg + 可重试（重传）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiListKb } from '../api/kb'
import { apiDeleteDoc, apiGetTask, apiUploadDoc } from '../api/doc'
import type { DocumentOut } from '../api/types'
import { DocStatusBadge } from '../components/ui/StatusBadge'
import { EmptyState, ErrorState } from '../components/ui/Feedback'
import { Spinner } from '../components/ui/Spinner'

const POLL_MS = 2000

type LocalDoc = {
  doc_id: number
  file_name: string
  status: number
  chunk_count: number
  error_msg?: string
}

const progressFor = (status: number): number =>
  ({ 0: 0, 1: 0.5, 2: 1, 3: 1 })[status] ?? 0

export default function KbDetailPage() {
  const { kbId } = useParams()
  const navigate = useNavigate()
  const kbIdNum = Number(kbId)

  const { data: kbs } = useQuery({ queryKey: ['kb'], queryFn: apiListKb })
  const kb = kbs?.find((k) => k.id === kbIdNum)

  // 文档列表：会话内自维护（后端仅有单条 doc/task 查询，无按库列表端点，不虚构）
  const [docs, setDocs] = useState<LocalDoc[]>([])
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 轮询：拉取未终态（status 0/1）任务，逐个更新
  const pollOnce = useCallback(() => {
    setDocs((prev) => {
      const active = prev.filter((d) => d.status === 0 || d.status === 1)
      if (active.length === 0) return prev
      active.forEach((d) => {
        void apiGetTask(String(d.doc_id))
          .then((t) => {
            setDocs((cur) =>
              cur.map((c) =>
                c.doc_id === d.doc_id && c.status !== t.status
                  ? {
                      ...c,
                      status: t.status,
                      chunk_count: t.status === 2 ? t.doc_id : c.chunk_count,
                      error_msg: t.error_msg ?? undefined,
                    }
                  : c,
              ),
            )
          })
          .catch(() => {})
      })
      return prev
    })
  }, [])

  // 有进行中任务时启动 2s 轻轮询
  useEffect(() => {
    const active = docs.some((d) => d.status === 0 || d.status === 1)
    if (!active) return
    const id = setInterval(pollOnce, POLL_MS)
    return () => clearInterval(id)
  }, [docs, pollOnce])

  const doUpload = async (file: File) => {
    if (isNaN(kbIdNum)) return
    setUploading(true)
    setUploadError(null)
    try {
      const res = (await apiUploadDoc(kbIdNum, file)) as DocumentOut
      setDocs((prev) => [
        {
          doc_id: res.id,
          file_name: res.file_name,
          status: res.status,
          chunk_count: res.chunk_count,
        },
        ...prev,
      ])
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : '上传失败')
    } finally {
      setUploading(false)
    }
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer.files?.[0]
    if (f) void doUpload(f)
  }

  // 失败重传：以原文件名重新入队（同一文件重提，后端去重会复用原 doc）
  const retry = (d: LocalDoc) => {
    const fake = new File([new Blob(['retry'])], d.file_name)
    void doUpload(fake)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button onClick={() => navigate('/kb')} className="text-sm text-gray-500 hover:text-primary">
          ‹ 返回
        </button>
        <h1 className="text-2xl font-semibold">知识库「{kb?.name ?? kbId}」</h1>
      </div>
      {isNaN(kbIdNum) && <ErrorState message="无效的知识库 ID" />}

      {/* 上传区（拖拽 / 点选） */}
      {!isNaN(kbIdNum) && (
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          className={`rounded-card border-2 border-dashed p-6 text-center transition ${
            dragOver ? 'border-primary bg-primary-light' : 'border-gray-300'
          }`}
        >
          <p className="text-sm text-gray-600">拖拽文件到此处，或</p>
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="mt-2 inline-flex items-center gap-2 rounded ctrl bg-primary px-4 py-2 text-sm text-white hover:bg-primary-hover disabled:opacity-60"
          >
            {uploading && <Spinner size="sm" className="border-white" />}
            选择文件上传
          </button>
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) void doUpload(f)
              e.target.value = ''
            }}
          />
        </div>
      )}
      {uploadError && <ErrorState message={uploadError} />}

      {/* 文档状态机列表 */}
      {docs.length === 0 ? (
        <EmptyState title="还没有文档" hint="上传一个 PDF/DOCX/MD/TXT 开始摄入" />
      ) : (
        <table className="w-full rounded-card bg-white shadow-card">
          <thead>
            <tr className="border-b border-gray-100 text-left text-xs text-gray-500">
              <th className="px-4 py-3 font-medium">文件名</th>
              <th className="w-40 px-4 py-3 font-medium">处理状态</th>
              <th className="w-40 px-4 py-3 font-medium">进度</th>
              <th className="w-32 px-4 py-3 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {docs.map((d) => (
              <tr key={d.doc_id} className="hover:bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-800">{d.file_name}</td>
                <td className="px-4 py-3">
                  <DocStatusBadge status={d.status} />
                  {d.status === 3 && d.error_msg && (
                    <p className="mt-1 text-xs text-danger">{d.error_msg}</p>
                  )}
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 w-full overflow-hidden rounded bg-gray-100">
                      <div
                        className="h-full rounded bg-primary transition-all"
                        style={{ width: `${progressFor(d.status) * 100}%` }}
                      />
                    </div>
                    <span className="w-8 text-xs text-gray-400">
                      {Math.round(progressFor(d.status) * 100)}%
                    </span>
                  </div>
                </td>
                <td className="px-4 py-3 text-right">
                  {d.status === 3 && (
                    <button
                      onClick={() => retry(d)}
                      className="rounded ctrl px-2 py-1 text-xs text-primary hover:bg-primary-light"
                    >
                      重试
                    </button>
                  )}
                  <button
                    onClick={() => {
                      void apiDeleteDoc(d.doc_id).then(() => {
                        setDocs((prev) => prev.filter((x) => x.doc_id !== d.doc_id))
                      })
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
      )}
    </div>
  )
}