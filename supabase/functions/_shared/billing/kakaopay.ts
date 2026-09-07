/**
 * `adapters/impl/kakaopay.py` 포팅 — KakaoPay open-api 정기결제.
 *
 * flow: `ready` → `approve`(SID 발급) → `subscribe`(월 배치) / `inactivate`(해지).
 * sandbox CID 는 `TCSUBSCRIP`. 운영 CID 는 심사 후 ENV 로 교체한다.
 *
 * ## 금액이 두 곳에 있다
 * `TOTAL_AMOUNT = 6900` 은 `plans.price_krw` 와 맞아야 한다. 원본도 상수로 두고 주석으로
 * 정합을 걸어 뒀다(결정 이력 #2). 여기서 바꾸면 청구액과 표시 가격이 갈린다.
 *
 * ## 오류는 전부 `PaymentError` 한 종류다
 * 배치가 이 예외를 잡아 해당 유저만 `past_due` 로 넘기고 다음 유저로 간다. 종류를
 * 늘리면 호출부가 분기해야 하는데, 원본은 분기하지 않는다.
 */

export class PaymentError extends Error {}

export const KAKAOPAY_BASE_URL = "https://open-api.kakaopay.com";
const ITEM_NAME = "Jet-Rag Pro 구독";
/** `plans.price_krw=6900` 과 정합 (결정 이력 #2). */
const TOTAL_AMOUNT = 6900;
const TIMEOUT_MS = 15_000;

export interface ReadyResult {
  tid: string;
  redirect_url: string;
}

export interface ApproveResult {
  sid: string;
  tid: string;
}

export interface KakaoPayConfig {
  secretKey: string;
  cid: string;
  baseUrl?: string;
  /** 테스트 주입 — 실제 KakaoPay 를 부르지 않는다. */
  fetchFn?: typeof fetch;
}

export class KakaoPayClient {
  #cid: string;
  #baseUrl: string;
  #headers: Record<string, string>;
  #fetch: typeof fetch;

  constructor(cfg: KakaoPayConfig) {
    if (!cfg.secretKey) {
      throw new Error("KakaoPay secret_key 미설정 — JETRAG_KAKAOPAY_SECRET_KEY 필요.");
    }
    this.#cid = cfg.cid;
    this.#baseUrl = (cfg.baseUrl ?? KAKAOPAY_BASE_URL).replace(/\/+$/, "");
    this.#headers = {
      "Authorization": `SECRET_KEY ${cfg.secretKey}`,
      "Content-Type": "application/json",
    };
    this.#fetch = cfg.fetchFn ?? fetch;
  }

  async #post(path: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    let resp: Response;
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
    try {
      resp = await this.#fetch(`${this.#baseUrl}${path}`, {
        method: "POST",
        headers: this.#headers,
        body: JSON.stringify(body),
        signal: ctl.signal,
      });
    } catch (e) {
      console.warn(`KakaoPay 네트워크 오류 (${path}): ${e}`);
      throw new PaymentError(`KakaoPay 네트워크 오류 (${path}): ${e}`);
    } finally {
      clearTimeout(timer);
    }

    const text = await resp.text();
    if (resp.status >= 400) {
      // **본문을 200 자로 자른다.** 이 문자열이 `payment_history.detail` 로 들어가고
      // 로그에도 남는다 — 카드사 응답에 개인정보가 섞여 나올 수 있어 원본이 자른다.
      console.warn(`KakaoPay ${resp.status} 오류 (${path})`);
      throw new PaymentError(`KakaoPay ${resp.status} (${path}): ${text.slice(0, 200)}`);
    }
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      throw new PaymentError(`KakaoPay 응답 파싱 실패 (${path}): ${text.slice(0, 200)}`);
    }
    // Python `isinstance(data, dict)` — 배열·숫자·null 을 전부 거른다.
    if (data === null || typeof data !== "object" || Array.isArray(data)) {
      throw new PaymentError(
        `KakaoPay 예상치 못한 응답 타입 (${path}): ${
          data === null ? "NoneType" : Array.isArray(data) ? "list" : typeof data
        }`,
      );
    }
    return data as Record<string, unknown>;
  }

  async ready(opts: {
    partnerOrderId: string;
    partnerUserId: string;
    approvalUrl: string;
    cancelUrl: string;
    failUrl: string;
  }): Promise<ReadyResult> {
    const data = await this.#post("/online/v1/payment/ready", {
      cid: this.#cid,
      partner_order_id: opts.partnerOrderId,
      partner_user_id: opts.partnerUserId,
      item_name: ITEM_NAME,
      quantity: 1,
      total_amount: TOTAL_AMOUNT,
      tax_free_amount: 0,
      approval_url: opts.approvalUrl,
      cancel_url: opts.cancelUrl,
      fail_url: opts.failUrl,
    });
    // 원본 `or` — PC URL 이 falsy(빈 문자열 포함)면 모바일 URL 로 넘어간다.
    const redirect = (data["next_redirect_pc_url"] as string) ||
      (data["next_redirect_mobile_url"] as string);
    if (!data["tid"] || !redirect) {
      throw new PaymentError(
        `KakaoPay ready 응답 불완전: keys=[${Object.keys(data).map((k) => `'${k}'`).join(", ")}]`,
      );
    }
    return { tid: String(data["tid"]), redirect_url: redirect };
  }

  async approve(opts: {
    tid: string;
    partnerOrderId: string;
    partnerUserId: string;
    pgToken: string;
  }): Promise<ApproveResult> {
    const data = await this.#post("/online/v1/payment/approve", {
      cid: this.#cid,
      tid: opts.tid,
      partner_order_id: opts.partnerOrderId,
      partner_user_id: opts.partnerUserId,
      pg_token: opts.pgToken,
    });
    const sid = data["sid"];
    if (!sid) {
      throw new PaymentError(
        "KakaoPay approve 응답에 sid 없음 — 정기결제 CID(TCSUBSCRIP 계열) 확인 필요.",
      );
    }
    return { sid: String(sid), tid: opts.tid };
  }

  async subscribe(opts: {
    sid: string;
    partnerOrderId: string;
    partnerUserId: string;
  }): Promise<void> {
    await this.#post("/online/v1/payment/subscription", {
      cid: this.#cid,
      sid: opts.sid,
      partner_order_id: opts.partnerOrderId,
      partner_user_id: opts.partnerUserId,
      item_name: ITEM_NAME,
      quantity: 1,
      total_amount: TOTAL_AMOUNT,
      tax_free_amount: 0,
    });
  }

  async inactivate(sid: string): Promise<void> {
    await this.#post("/online/v1/payment/manage/subscription/inactive", {
      cid: this.#cid,
      sid,
    });
  }
}

/** 원본 `get_payment_provider` — provider 가 kakaopay 가 아니면 던진다. */
export function getPaymentProvider(cfg: {
  paymentProvider: string;
  kakaopaySecretKey: string;
  kakaopayCid: string;
  fetchFn?: typeof fetch;
}): KakaoPayClient {
  const provider = (cfg.paymentProvider || "kakaopay").trim().toLowerCase();
  if (provider !== "kakaopay") {
    throw new Error(`알 수 없는 결제 provider: '${provider}'`);
  }
  return new KakaoPayClient({
    secretKey: cfg.kakaopaySecretKey,
    cid: cfg.kakaopayCid,
    fetchFn: cfg.fetchFn,
  });
}
