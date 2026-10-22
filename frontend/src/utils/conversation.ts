import type { Message } from '@/types';

// The customer's number or address on this message.
export function farEndOf(message: Message): string {
  return message.direction === 'inbound' ? message.sender_id : message.recipient_id;
}

// The customer a message belongs to, as the conversations endpoint groups them:
// the linked contact, else the far end of the message.
export function customerOf(message: Message): string {
  return message.contact_id || farEndOf(message);
}

// The conversation on screen in the inbox; its new messages need no alert.
let openCustomer: string | null = null;

export const openConversation = {
  get: () => openCustomer,
  set: (customer: string | null) => {
    openCustomer = customer;
  },
};
