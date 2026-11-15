/**
 * User video asset API client.
 */

import apiClient from './client'

export interface VideoAsset {
  asset_id: string
  filename: string
  content_type: string
  file_size: number
  width: number | null
  height: number | null
  duration: number | null
  original_url: string
  thumbnail_url: string
  source: string
}

export async function uploadVideoAssets(files: File[]): Promise<VideoAsset[]> {
  const formData = new FormData()
  files.forEach((file) => formData.append('files', file))

  const response: any = await apiClient.post('/video-assets', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
  })
  return response?.data ?? response ?? []
}

export async function listVideoAssets(limit?: number): Promise<VideoAsset[]> {
  const response: any = await apiClient.get('/video-assets', {
    params: limit && limit > 0 ? { limit } : undefined,
  })
  return response?.data ?? response ?? []
}

export async function deleteVideoAsset(assetId: string): Promise<void> {
  await apiClient.delete(`/video-assets/${encodeURIComponent(assetId)}`)
}

export const videoAssetsApi = {
  uploadVideoAssets,
  listVideoAssets,
  deleteVideoAsset,
}