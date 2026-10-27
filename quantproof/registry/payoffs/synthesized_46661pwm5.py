def cashflows(perf, terms):
    import numpy as np

    perf = np.asarray(perf, dtype=float)
    n_paths = perf.shape[0]
    n_dates = perf.shape[1]
    cash = np.zeros((n_paths, n_dates), dtype=float)

    notional = float(terms["notional"])
    final_index = int(terms["final_index"])
    autocall = terms["autocall"]
    coupon = terms["coupon"]
    downside = terms["downside"]
    upside = terms["upside"]

    downside_kind = str(downside["kind"]).lower() if downside is not None else ""
    downside_level = float(downside["level"]) if downside is not None else 0.0
    downside_gearing = float(downside["gearing"]) if downside is not None and downside["gearing"] is not None else 1.0
    buffer_amount = 1.0 - downside_level if "buffer" in downside_kind else 0.0

    leverage = 1.0
    cap = None
    if upside is not None:
        if upside["leverage"] is not None:
            leverage = float(upside["leverage"])
        if upside["cap"] is not None:
            cap = float(upside["cap"])

    called = np.zeros(n_paths, dtype=bool)
    missed_coupons = np.zeros(n_paths, dtype=float)

    for k in range(n_dates):
        active = np.logical_not(called)
        if np.any(active):
            worst_k = np.min(perf[:, k, :], axis=1)
        else:
            worst_k = np.zeros(n_paths, dtype=float)

        if k == final_index:
            worst = worst_k
            ret = worst - 1.0
            payment = np.zeros(n_paths, dtype=float)

            positive = ret > 0.0
            if np.any(positive):
                upside_return = ret * leverage
                if cap is not None:
                    upside_return = np.minimum(upside_return, cap)
                payment[positive] = notional * (1.0 + upside_return[positive])

            buffered = np.logical_and(ret <= 0.0, ret >= -buffer_amount)
            if np.any(buffered):
                payment[buffered] = notional * (1.0 + np.abs(ret[buffered]))

            below = ret < -buffer_amount
            if np.any(below):
                payment[below] = notional * (1.0 + downside_gearing * (ret[below] + buffer_amount))

            payment = np.maximum(payment, 0.0)

            if coupon is not None and coupon["amounts"][k] is not None:
                coupon_barrier = float(coupon["barrier"])
                coupon_amount = float(coupon["amounts"][k])
                eligible = worst_k >= coupon_barrier
                if bool(coupon["memory"]):
                    coupon_payment = np.where(eligible, coupon_amount * (1.0 + missed_coupons), 0.0)
                    payment = payment + coupon_payment
                else:
                    payment = payment + np.where(eligible, coupon_amount, 0.0)

            cash[active, k] = payment[active]
            called = np.logical_or(called, active)
        else:
            call_paid = np.zeros(n_paths, dtype=bool)
            if autocall is not None and autocall["call_amounts"][k] is not None:
                trigger = float(autocall["trigger"])
                call_amount = float(autocall["call_amounts"][k])
                eligible = np.min(perf[:, k, :], axis=1) >= trigger
                call_paid = np.logical_and(active, eligible)
                cash[call_paid, k] = call_amount
                called = np.logical_or(called, call_paid)

            if coupon is not None and coupon["amounts"][k] is not None:
                remaining = np.logical_and(active, np.logical_not(call_paid))
                if np.any(remaining):
                    coupon_barrier = float(coupon["barrier"])
                    coupon_amount = float(coupon["amounts"][k])
                    eligible = worst_k >= coupon_barrier
                    if bool(coupon["memory"]):
                        pay_mask = np.logical_and(remaining, eligible)
                        cash[pay_mask, k] = cash[pay_mask, k] + coupon_amount * (1.0 + missed_coupons[pay_mask])
                        missed_coupons[pay_mask] = 0.0
                        miss_mask = np.logical_and(remaining, np.logical_not(eligible))
                        missed_coupons[miss_mask] = missed_coupons[miss_mask] + 1.0
                    else:
                        pay_mask = np.logical_and(remaining, eligible)
                        cash[pay_mask, k] = cash[pay_mask, k] + coupon_amount

    return cash