import { ref } from 'vue'
import { uploadAssets, type ImageAsset } from '@/api/assets'

/**
 * 待发送图片附件 — ChatView 拆出
 *
 * 上传走 /assets，拿回 asset_id 随消息一起发出（后端仍走 image_asset_ids），
 * 前端只留缩略信息用于渲染。上限 10 张。
 *
 * 两条入口共用同一个上传通道：文件选择器 + 输入框粘贴（截图/网页图片）。
 */
const MAX_IMAGES = 10

export function useChatImages() {
  const pendingImageAssets = ref<ImageAsset[]>([])
  const imageUploadRef = ref<HTMLInputElement | null>(null)
  const uploadingImages = ref(false)

  function triggerImageUpload() {
    imageUploadRef.value?.click()
  }

  async function uploadImageFiles(files: File[]) {
    if (!files.length) return
    if (uploadingImages.value) return
    if (pendingImageAssets.value.length + files.length > MAX_IMAGES) {
      alert(`最多附带 ${MAX_IMAGES} 张图片`)
      return
    }
    uploadingImages.value = true
    try {
      const assets = await uploadAssets(files)
      pendingImageAssets.value = [...pendingImageAssets.value, ...assets]
    } catch (error) {
      alert(`图片上传失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      uploadingImages.value = false
    }
  }

  async function onImageFilesSelected(event: Event) {
    const input = event.target as HTMLInputElement
    const files = Array.from(input.files || [])
    input.value = ''
    await uploadImageFiles(files)
  }

  /**
   * 粘贴图片：截图 / 从网页或设计工具复制的图片直接进待选区。
   *
   * 剪贴板里的图片是 `kind === 'file'` 的 blob，拿不到文件名的场景很常见，
   * 这里补一个带扩展名的名字（后端只用它做展示，不参与存储路径）。
   * 命中图片时必须 preventDefault，否则浏览器会把图片按 base64 文本塞进输入框。
   */
  async function onPasteImages(event: ClipboardEvent) {
    const items = event.clipboardData?.items
    if (!items || items.length === 0) return
    const files: File[] = []
    for (const item of Array.from(items)) {
      if (item.kind !== 'file' || !item.type.startsWith('image/')) continue
      const blob = item.getAsFile()
      if (!blob) continue
      const ext = (item.type.split('/')[1] || 'png').replace('jpeg', 'jpg')
      files.push(
        blob.name
          ? blob
          : new File([blob], `pasted-${Date.now()}-${files.length}.${ext}`, { type: item.type }),
      )
    }
    if (!files.length) return
    event.preventDefault()
    await uploadImageFiles(files)
  }

  function removePendingImage(idx: number) {
    pendingImageAssets.value.splice(idx, 1)
  }

  return {
    pendingImageAssets,
    imageUploadRef,
    uploadingImages,
    triggerImageUpload,
    onImageFilesSelected,
    onPasteImages,
    removePendingImage,
  }
}