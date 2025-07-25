import type { FC } from 'react'
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import useSWR from 'swr'
import { useContext } from 'use-context-selector'
import { useTranslation } from 'react-i18next'
import { omit } from 'lodash-es'
import { RiLoader2Line, RiPauseCircleLine, RiPlayCircleLine } from '@remixicon/react'
import Image from 'next/image'
import { FieldInfo } from '../metadata'
import { useDocumentContext } from '../index'
import { IndexingType } from '../../../create/step-two'
import { indexMethodIcon, retrievalIcon } from '../../../create/icons'
import EmbeddingSkeleton from './skeleton'
import { RETRIEVE_METHOD } from '@/types/app'
import cn from '@/utils/classnames'
import Divider from '@/app/components/base/divider'
import { ToastContext } from '@/app/components/base/toast'
import type { IndexingStatusResponse } from '@/models/datasets'
import { ProcessMode, type ProcessRuleResponse } from '@/models/datasets'
import type { CommonResponse } from '@/models/common'
import { asyncRunSafe, sleep } from '@/utils'
import {
  fetchIndexingStatus as doFetchIndexingStatus,
  fetchProcessRule,
  pauseDocIndexing,
  resumeDocIndexing,
} from '@/service/datasets'
// 在文件顶部添加事件总线引用
import eventBus from '@/event-bus';

// 在组件内部添加状态管理
const [pollingActive, setPollingActive] = useState(true);

// 修改 pollIndexingStatus 函数
const pollIndexingStatus = async () => {
  // 添加退出检查 - 核心修改点
  if (!pollingActive) return;

  try {
    const response = await fetchIndexingStatus();
    // ... 原有处理逻辑 ...
  } catch (error) {
    // 401错误特殊处理 - 核心修改点
    if (error?.status === 401) {
      setPollingActive(false);
      clearTimeout(pollingTimer);

      showToast({
        message: t('datasetDocuments.embedding.canceledByLogout'),
        type: 'error',
        closable: true, // 允许手动关闭
        duration: 5000 // 5秒后自动关闭
      });
      return;
    }
    // ... 其他错误处理 ...
  }

  // 仅当未退出时继续轮询
  if (pollingActive) {
    pollingTimer = setTimeout(pollIndexingStatus, 3000);
  }
};

// 添加用户退出事件监听 - 核心修改点
useEffect(() => {
  const handleLogout = () => {
    setPollingActive(false);
    if (pollingTimer) clearTimeout(pollingTimer);
  };

  eventBus.on('user_logged_out', handleLogout);

  return () => {
    eventBus.off('user_logged_out', handleLogout);
    handleLogout(); // 组件卸载时停止轮询
  };
}, []);

type IEmbeddingDetailProps = {
  datasetId?: string
  documentId?: string
  indexingType?: IndexingType
  retrievalMethod?: RETRIEVE_METHOD
  detailUpdate: VoidFunction
}

type IRuleDetailProps = {
  sourceData?: ProcessRuleResponse
  indexingType?: IndexingType
  retrievalMethod?: RETRIEVE_METHOD
}

const RuleDetail: FC<IRuleDetailProps> = React.memo(({
  sourceData,
  indexingType,
  retrievalMethod,
}) => {
  const { t } = useTranslation()

  const segmentationRuleMap = {
    mode: t('datasetDocuments.embedding.mode'),
    segmentLength: t('datasetDocuments.embedding.segmentLength'),
    textCleaning: t('datasetDocuments.embedding.textCleaning'),
  }

  const getRuleName = (key: string) => {
    if (key === 'remove_extra_spaces')
      return t('datasetCreation.stepTwo.removeExtraSpaces')

    if (key === 'remove_urls_emails')
      return t('datasetCreation.stepTwo.removeUrlEmails')

    if (key === 'remove_stopwords')
      return t('datasetCreation.stepTwo.removeStopwords')
  }

  const isNumber = (value: unknown) => {
    return typeof value === 'number'
  }

  const getValue = useCallback((field: string) => {
    let value: string | number | undefined = '-'
    const maxTokens = isNumber(sourceData?.rules?.segmentation?.max_tokens)
      ? sourceData.rules.segmentation.max_tokens
      : value
    const childMaxTokens = isNumber(sourceData?.rules?.subchunk_segmentation?.max_tokens)
      ? sourceData.rules.subchunk_segmentation.max_tokens
      : value
    switch (field) {
      case 'mode':
        value = !sourceData?.mode
          ? value
          : sourceData.mode === ProcessMode.general
            ? (t('datasetDocuments.embedding.custom') as string)
            : `${t('datasetDocuments.embedding.hierarchical')} · ${sourceData?.rules?.parent_mode === 'paragraph'
              ? t('dataset.parentMode.paragraph')
              : t('dataset.parentMode.fullDoc')}`
        break
      case 'segmentLength':
        value = !sourceData?.mode
          ? value
          : sourceData.mode === ProcessMode.general
            ? maxTokens
            : `${t('datasetDocuments.embedding.parentMaxTokens')} ${maxTokens}; ${t('datasetDocuments.embedding.childMaxTokens')} ${childMaxTokens}`
        break
      default:
        value = !sourceData?.mode
          ? value
          : sourceData?.rules?.pre_processing_rules?.filter(rule =>
            rule.enabled).map(rule => getRuleName(rule.id)).join(',')
        break
    }
    return value
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceData])

  return <div className='py-3'>
    <div className='flex flex-col gap-y-1'>
      {Object.keys(segmentationRuleMap).map((field) => {
        return <FieldInfo
          key={field}
          label={segmentationRuleMap[field as keyof typeof segmentationRuleMap]}
          displayedValue={String(getValue(field))}
        />
      })}
    </div>
    <Divider type='horizontal' className='bg-divider-subtle' />
    <FieldInfo
      label={t('datasetCreation.stepTwo.indexMode')}
      displayedValue={t(`datasetCreation.stepTwo.${indexingType === IndexingType.ECONOMICAL ? 'economical' : 'qualified'}`) as string}
      valueIcon={
        <Image
          className='size-4'
          src={
            indexingType === IndexingType.ECONOMICAL
              ? indexMethodIcon.economical
              : indexMethodIcon.high_quality
          }
          alt=''
        />
      }
    />
    <FieldInfo
      label={t('datasetSettings.form.retrievalSetting.title')}
      displayedValue={t(`dataset.retrieval.${indexingType === IndexingType.ECONOMICAL ? 'invertedIndex' : retrievalMethod}.title`) as string}
      valueIcon={
        <Image
          className='size-4'
          src={
            retrievalMethod === RETRIEVE_METHOD.fullText
              ? retrievalIcon.fullText
              : retrievalMethod === RETRIEVE_METHOD.hybrid
                ? retrievalIcon.hybrid
                : retrievalIcon.vector
          }
          alt=''
        />
      }
    />
  </div>
})

RuleDetail.displayName = 'RuleDetail'

const EmbeddingDetail: FC<IEmbeddingDetailProps> = ({
  datasetId: dstId,
  documentId: docId,
  detailUpdate,
  indexingType,
  retrievalMethod,
}) => {
  const { t } = useTranslation()
  const { notify } = useContext(ToastContext)

  const datasetId = useDocumentContext(s => s.datasetId)
  const documentId = useDocumentContext(s => s.documentId)
  const localDatasetId = dstId ?? datasetId
  const localDocumentId = docId ?? documentId

  const [indexingStatusDetail, setIndexingStatusDetail] = useState<IndexingStatusResponse | null>(null)
  const fetchIndexingStatus = async () => {
    const status = await doFetchIndexingStatus({ datasetId: localDatasetId, documentId: localDocumentId })
    setIndexingStatusDetail(status)
    return status
  }

  const isStopQuery = useRef(false)
  const stopQueryStatus = useCallback(() => {
    isStopQuery.current = true
  }, [])

  const startQueryStatus = useCallback(async () => {
    if (isStopQuery.current)
      return

    try {
      const indexingStatusDetail = await fetchIndexingStatus()
      if (['completed', 'error', 'paused'].includes(indexingStatusDetail?.indexing_status)) {
        stopQueryStatus()
        detailUpdate()
        return
      }

      await sleep(2500)
      await startQueryStatus()
    }
    catch {
      await sleep(2500)
      await startQueryStatus()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stopQueryStatus])

  useEffect(() => {
    isStopQuery.current = false
    startQueryStatus()
    return () => {
      stopQueryStatus()
    }
  }, [startQueryStatus, stopQueryStatus])

  const { data: ruleDetail } = useSWR({
    action: 'fetchProcessRule',
    params: { documentId: localDocumentId },
  }, apiParams => fetchProcessRule(omit(apiParams, 'action')), {
    revalidateOnFocus: false,
  })

  const isEmbedding = useMemo(() => ['indexing', 'splitting', 'parsing', 'cleaning'].includes(indexingStatusDetail?.indexing_status || ''), [indexingStatusDetail])
  const isEmbeddingCompleted = useMemo(() => ['completed'].includes(indexingStatusDetail?.indexing_status || ''), [indexingStatusDetail])
  const isEmbeddingPaused = useMemo(() => ['paused'].includes(indexingStatusDetail?.indexing_status || ''), [indexingStatusDetail])
  const isEmbeddingError = useMemo(() => ['error'].includes(indexingStatusDetail?.indexing_status || ''), [indexingStatusDetail])
  const percent = useMemo(() => {
    const completedCount = indexingStatusDetail?.completed_segments || 0
    const totalCount = indexingStatusDetail?.total_segments || 0
    if (totalCount === 0)
      return 0
    const percent = Math.round(completedCount * 100 / totalCount)
    return percent > 100 ? 100 : percent
  }, [indexingStatusDetail])

  const handleSwitch = async () => {
    const opApi = isEmbedding ? pauseDocIndexing : resumeDocIndexing
    const [e] = await asyncRunSafe<CommonResponse>(opApi({ datasetId: localDatasetId, documentId: localDocumentId }) as Promise<CommonResponse>)
    if (!e) {
      notify({ type: 'success', message: t('common.actionMsg.modifiedSuccessfully') })
      // if the embedding is resumed from paused, we need to start the query status
      if (isEmbeddingPaused) {
        isStopQuery.current = false
        startQueryStatus()
        detailUpdate()
      }
      setIndexingStatusDetail(null)
    }
    else {
      notify({ type: 'error', message: t('common.actionMsg.modifiedUnsuccessfully') })
    }
  }

  return (
    <>
      <div className='flex flex-col gap-y-2 px-16 py-12'>
        <div className='flex h-6 items-center gap-x-1'>
          {isEmbedding && <RiLoader2Line className='h-4 w-4 animate-spin text-text-secondary' />}
          <span className='system-md-semibold-uppercase grow text-text-secondary'>
            {isEmbedding && t('datasetDocuments.embedding.processing')}
            {isEmbeddingCompleted && t('datasetDocuments.embedding.completed')}
            {isEmbeddingPaused && t('datasetDocuments.embedding.paused')}
            {isEmbeddingError && t('datasetDocuments.embedding.error')}
          </span>
          {isEmbedding && (
            <button
              type='button'
              className={`flex items-center gap-x-1 rounded-md border-[0.5px]
              border-components-button-secondary-border bg-components-button-secondary-bg px-1.5 py-1 shadow-xs shadow-shadow-shadow-3 backdrop-blur-[5px]`}
              onClick={handleSwitch}
            >
              <RiPauseCircleLine className='h-3.5 w-3.5 text-components-button-secondary-text' />
              <span className='system-xs-medium pr-[3px] text-components-button-secondary-text'>
                {t('datasetDocuments.embedding.pause')}
              </span>
            </button>
          )}
          {isEmbeddingPaused && (
            <button
              type='button'
              className={`flex items-center gap-x-1 rounded-md border-[0.5px]
              border-components-button-secondary-border bg-components-button-secondary-bg px-1.5 py-1 shadow-xs shadow-shadow-shadow-3 backdrop-blur-[5px]`}
              onClick={handleSwitch}
            >
              <RiPlayCircleLine className='h-3.5 w-3.5 text-components-button-secondary-text' />
              <span className='system-xs-medium pr-[3px] text-components-button-secondary-text'>
                {t('datasetDocuments.embedding.resume')}
              </span>
            </button>
          )}
        </div>
        {/* progress bar */}
        <div className={cn(
          'flex h-2 w-full items-center overflow-hidden rounded-md border border-components-progress-bar-border',
          isEmbedding ? 'bg-components-progress-bar-bg bg-opacity-50' : 'bg-components-progress-bar-bg',
        )}>
          <div
            className={cn(
              'h-full',
              (isEmbedding || isEmbeddingCompleted) && 'bg-components-progress-bar-progress-solid',
              (isEmbeddingPaused || isEmbeddingError) && 'bg-components-progress-bar-progress-highlight',
            )}
            style={{ width: `${percent}%` }}
          />
        </div>
        <div className={'flex w-full items-center'}>
          <span className='system-xs-medium text-text-secondary'>
            {`${t('datasetDocuments.embedding.segments')} ${indexingStatusDetail?.completed_segments || '--'}/${indexingStatusDetail?.total_segments || '--'} · ${percent}%`}
          </span>
        </div>
        <RuleDetail sourceData={ruleDetail} indexingType={indexingType} retrievalMethod={retrievalMethod} />
      </div>
      <EmbeddingSkeleton />
    </>
  )
}

export default React.memo(EmbeddingDetail)
