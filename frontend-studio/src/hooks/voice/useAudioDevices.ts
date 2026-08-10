import { useCallback, useEffect, useState } from 'react'

const INPUT_DEVICE_KEY = 'agent-flow.voice.input-device'
const OUTPUT_DEVICE_KEY = 'agent-flow.voice.output-device'

export interface AudioDeviceOption {
  deviceId: string
  label: string
}

function readStoredDevice(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}

function storeDevice(key: string, deviceId: string) {
  try {
    if (deviceId) window.localStorage.setItem(key, deviceId)
    else window.localStorage.removeItem(key)
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}

function namedDevices(devices: MediaDeviceInfo[], kind: MediaDeviceKind, fallback: string) {
  return devices
    .filter((device) => device.kind === kind && device.deviceId !== 'default')
    .map((device, index) => ({
      deviceId: device.deviceId,
      label: device.label || `${fallback} ${index + 1}`,
    }))
}

/** Browser-only audio device discovery, selection persistence, and fallback. */
export function useAudioDevices() {
  const [inputDevices, setInputDevices] = useState<AudioDeviceOption[]>([])
  const [outputDevices, setOutputDevices] = useState<AudioDeviceOption[]>([])
  const [inputDeviceId, setInputDeviceState] = useState(() => readStoredDevice(INPUT_DEVICE_KEY))
  const [outputDeviceId, setOutputDeviceState] = useState(() => readStoredDevice(OUTPUT_DEVICE_KEY))
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const selectInputDevice = useCallback((deviceId: string) => {
    setInputDeviceState(deviceId)
    storeDevice(INPUT_DEVICE_KEY, deviceId)
    setNotice(null)
  }, [])

  const selectOutputDevice = useCallback((deviceId: string) => {
    setOutputDeviceState(deviceId)
    storeDevice(OUTPUT_DEVICE_KEY, deviceId)
    setNotice(null)
  }, [])

  const refresh = useCallback(async (validateSelection = false) => {
    if (!navigator.mediaDevices?.enumerateDevices) {
      setError('当前浏览器不支持音频设备枚举，将使用系统默认设备')
      setLoading(false)
      return
    }
    try {
      const devices = await navigator.mediaDevices.enumerateDevices()
      const inputs = namedDevices(devices, 'audioinput', '麦克风')
      const outputs = namedDevices(devices, 'audiooutput', '扬声器')
      setInputDevices(inputs)
      setOutputDevices(outputs)
      setError(null)

      if (validateSelection) {
        setInputDeviceState((current) => {
          if (!current || inputs.some((device) => device.deviceId === current)) return current
          storeDevice(INPUT_DEVICE_KEY, '')
          setNotice('原麦克风已不可用，已自动切换为跟随系统')
          return ''
        })
        setOutputDeviceState((current) => {
          if (!current || outputs.some((device) => device.deviceId === current)) return current
          storeDevice(OUTPUT_DEVICE_KEY, '')
          setNotice('原扬声器已不可用，已自动切换为跟随系统')
          return ''
        })
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '无法读取音频设备')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh(false)
    const mediaDevices = navigator.mediaDevices
    if (!mediaDevices?.addEventListener) return
    const onDeviceChange = () => void refresh(true)
    mediaDevices.addEventListener('devicechange', onDeviceChange)
    return () => mediaDevices.removeEventListener('devicechange', onDeviceChange)
  }, [refresh])

  const refreshAfterPermission = useCallback(() => refresh(true), [refresh])
  const fallbackInput = useCallback(() => selectInputDevice(''), [selectInputDevice])

  return {
    inputDevices,
    outputDevices,
    inputDeviceId,
    outputDeviceId,
    selectInputDevice,
    selectOutputDevice,
    refreshAfterPermission,
    fallbackInput,
    loading,
    error,
    notice,
    outputSelectionSupported:
      typeof AudioContext !== 'undefined' && 'setSinkId' in AudioContext.prototype,
  }
}
