import { ref } from 'vue'
import { uploadVideoAssets, type VideoAsset } from '@/api/videoAssets'

/**
 * 待发送视频附件 — ChatView 拆出
 *
 * 上传走 /api/video-assets，拿回 asset_id 随消息一起发出（后端走 video_asset_ids），
 * 前端只留缩略信息用于渲染。上限 5 个视频。
 */
export function useChatVideos() {
  const pendingVideoAssets = ref<VideoAsset[]>([])
  const videoUploadRef = ref<HTMLInputElement | null>(null)
  const uploadingVideos = ref(false)

  function triggerVideoUpload() {
    videoUploadRef.value?.click()
  }

  async function onVideoFilesSelected(event: Event) {
    const input = event.target as HTMLInputElement
    const files = Array.from(input.files || [])
    input.value = ''
    if (!files.length) return
    if (uploadingVideos.value) return
    if (pendingVideoAssets.value.length + files.length > 5) {
      alert('最多附带 5 个视频')
      return
    }
    const oversized = files.find(f => f.size > 500 * 1024 * 1024)
    if (oversized) {
      alert(`视频 ${oversized.name} 超过 500MB 限制`)
      return
    }
    uploadingVideos.value = true
    try {
      const assets = await uploadVideoAssets(files)
      pendingVideoAssets.value = [...pendingVideoAssets.value, ...assets]
    } catch (error) {
      alert(`视频上传失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      uploadingVideos.value = false
    }
  }

  function removePendingVideo(idx: number) {
    pendingVideoAssets.value.splice(idx, 1)
  }

  return {
    pendingVideoAssets,
    videoUploadRef,
    uploadingVideos,
    triggerVideoUpload,
    onVideoFilesSelected,
    removePendingVideo,
  }
}