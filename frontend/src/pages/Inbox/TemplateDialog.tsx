import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import axios from 'axios';
import type { TFunction } from 'i18next';
import Modal from '@/components/common/Modal';
import { messagesApi } from '@/services/messages';
import type { ApiError, WhatsAppTemplate } from '@/types';
import { claimedMessage } from './ReplyBox';

interface Props {
  contactId: string;
  contactName: string;
  onClose: () => void;
  onSent: () => void;
}

type TemplateList =
  | { state: 'loading' }
  | { state: 'failed'; notice: string }
  | { state: 'ready'; templates: WhatsAppTemplate[] };

function listErrorMessage(error: unknown, t: TFunction<'inbox'>) {
  if (axios.isAxiosError<ApiError>(error) && error.response?.data?.error?.code === 'NOT_CONFIGURED') return t('chatbotNotConfigured');
  return t('templatesLoadFailed');
}

function sendErrorMessage(error: unknown, t: TFunction<'inbox'>) {
  if (!axios.isAxiosError<ApiError>(error) || !error.response) return t('sendUnknownResult');
  const apiError = error.response.data?.error;
  if (!apiError) return t('sendFailed');
  const fields = apiError.fields || {};
  switch (apiError.code) {
    case 'NO_OPT_IN': return t('templateNoOptIn');
    case 'TEMPLATE_PENDING': return t('templatePending');
    case 'CLAIMED': return claimedMessage(apiError.fields, t);
    case 'TEMPLATE_UNKNOWN': return t('templateUnknown');
    case 'TEMPLATE_PARAMS': return t('templateParamsInvalid');
    case 'SEND_FAILED': return fields.error_code ? t('metaRejected', { code: fields.error_code, title: fields.error_title || '' }) : t('sendNotSent');
    case 'CHATBOT_FAILED': return t('chatbotFailed');
    case 'NOT_CONFIGURED': return t('chatbotNotConfigured');
    case 'NOT_ASSIGNED': return t('notAssigned');
    case 'NO_PHONE': return t('noPhone');
    default: return t('sendFailed');
  }
}

// Each {{n}} becomes the n-th parameter, trimmed, as the backend renders it.
function renderBody(body: string, params: string[]) {
  return body.replace(/\{\{(\d+)\}\}/g, (placeholder, index: string) => {
    const value = params[Number(index) - 1];
    return value === undefined ? placeholder : value.trim();
  });
}

const templateKey = (template: WhatsAppTemplate) => `${template.name}\u0000${template.language}`;

export default function TemplateDialog({ contactId, contactName, onClose, onSent }: Props) {
  const { t } = useTranslation('inbox');
  const [list, setList] = useState<TemplateList>({ state: 'loading' });
  const [selectedKey, setSelectedKey] = useState('');
  const [params, setParams] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState('');
  const active = useRef(true);

  useEffect(() => {
    active.current = true;
    void messagesApi.getWhatsAppTemplates().then(
      (response) => { if (active.current) setList({ state: 'ready', templates: response.data.data ?? [] }); },
      (error: unknown) => { if (active.current) setList({ state: 'failed', notice: listErrorMessage(error, t) }); },
    );
    return () => { active.current = false; };
  }, [t]);

  const templates = list.state === 'ready' ? list.templates : [];
  const selected = templates.find((template) => templateKey(template) === selectedKey) ?? null;
  const hasEmptyParam = params.some((value) => !value.trim());

  const selectTemplate = (key: string) => {
    setSelectedKey(key);
    setNotice('');
    const template = templates.find((candidate) => templateKey(candidate) === key);
    setParams(template ? template.params.map((param, index) => (index === 0 ? contactName.slice(0, param.max_len) : '')) : []);
  };

  const close = () => {
    if (!sending) onClose();
  };

  const send = async () => {
    if (!selected || sending || hasEmptyParam) return;
    setSending(true);
    setNotice('');
    try {
      const response = await messagesApi.sendWhatsAppTemplate(contactId, selected.name, selected.language, params);
      if (!active.current) return;
      onClose();
      onSent();
      if (response.status === 202) toast(t('sentUnconfirmed'));
    } catch (error: unknown) {
      if (!active.current) return;
      // No response: the message may be stored, so the conversation is read again.
      if (!axios.isAxiosError(error) || !error.response) onSent();
      setNotice(sendErrorMessage(error, t));
    } finally {
      if (active.current) setSending(false);
    }
  };

  return (
    <Modal open onClose={close} title={t('templateDialogTitle')}>
      <div className="space-y-3">
        {list.state === 'loading' && <p className="text-sm text-text-muted">{t('templatesLoading')}</p>}
        {list.state === 'failed' && <p className="text-sm text-text-secondary">{list.notice}</p>}
        {list.state === 'ready' && templates.length === 0 && <p className="text-sm text-text-muted">{t('templatesEmpty')}</p>}
        {templates.length > 0 && (
          <select className="input text-sm" value={selectedKey} disabled={sending} onChange={(event) => selectTemplate(event.target.value)}>
            <option value="" disabled>{t('templateSelect')}</option>
            {templates.map((template) => (
              <option key={templateKey(template)} value={templateKey(template)}>
                {[template.name, template.language, template.category].filter(Boolean).join(' · ')}
              </option>
            ))}
          </select>
        )}
        {selected && (
          <>
            {selected.params.map((param, index) => (
              <label key={index} className="block space-y-1">
                <span className="text-xs text-text-secondary">{param.label}</span>
                <input
                  className="input text-sm"
                  maxLength={param.max_len}
                  disabled={sending}
                  value={params[index] ?? ''}
                  onChange={(event) => setParams((current) => current.map((value, position) => (position === index ? event.target.value : value)))}
                />
              </label>
            ))}
            <div>
              <p className="text-xs font-medium text-text-secondary">{t('templatePreview')}</p>
              <p className="mt-1 rounded-lg bg-dark-hover p-3 text-sm whitespace-pre-wrap break-words text-text-primary">{renderBody(selected.body, params)}</p>
            </div>
            {hasEmptyParam && <p className="text-xs text-text-muted">{t('templateParamsRequired')}</p>}
          </>
        )}
        {notice && <p className="text-xs text-text-secondary">{notice}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary text-sm" disabled={sending} onClick={close}>{t('cancel')}</button>
          <button type="button" className="btn-primary px-4 text-sm" disabled={!selected || sending || hasEmptyParam} onClick={send}>{t('send')}</button>
        </div>
      </div>
    </Modal>
  );
}
