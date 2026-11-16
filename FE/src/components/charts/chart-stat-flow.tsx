"use client";

import { type ReactNode, useMemo } from "react";
import { cn } from "@/lib/utils";

/** Subset of `Intl.NumberFormatOptions` used to format the value */
export interface ChartStatFlowFormat {
  notation?: "standard" | "compact";
  compactDisplay?: "short" | "long";
  minimumFractionDigits?: number;
  maximumFractionDigits?: number;
  minimumIntegerDigits?: number;
  minimumSignificantDigits?: number;
  maximumSignificantDigits?: number;
  style?: "decimal" | "percent" | "currency";
  currency?: string;
  currencyDisplay?: "symbol" | "narrowSymbol" | "code" | "name";
  unit?: string;
  unitDisplay?: "short" | "long" | "narrow";
}

export const defaultChartStatFlowFormat: ChartStatFlowFormat = {
  notation: "standard",
  maximumFractionDigits: 0,
};

function formatStatValue(
  value: number,
  formatOptions: ChartStatFlowFormat,
  prefix?: string,
  suffix?: string
): string {
  const formatted = new Intl.NumberFormat(undefined, formatOptions).format(
    value
  );
  return `${prefix ?? ""}${formatted}${suffix ?? ""}`;
}

export interface ChartStatFlowProps {
  value: number;
  label: string;
  formatOptions?: ChartStatFlowFormat;
  prefix?: string;
  suffix?: string;
  valueClassName?: string;
  labelClassName?: string;
  icon?: ReactNode;
}

/**
 * Shared value + label stack (same layout as pie / ring centers).
 * Parent should provide flex alignment and sizing when needed.
 *
 * 숫자는 NumberFlow(@number-flow/react) 없이 서식을 적용한 글자로 바로 그린다. NumberFlow 는 자기 요소 안에 <style>
 * 태그를 만들어 넣어서, 운영 CSP(style-src 에 'unsafe-inline' 없음)에서는 스타일이 막혀 "01234567890123456789개"처럼
 * 깨진다. 숫자가 굴러가는 효과는 없고, 링에 마우스를 올리면 숫자와 이름이 함께 바로 바뀐다.
 */
export function ChartStatFlow({
  value,
  label,
  formatOptions = defaultChartStatFlowFormat,
  prefix,
  suffix,
  valueClassName = "text-2xl font-bold",
  labelClassName = "text-xs",
  icon,
}: ChartStatFlowProps) {
  const staticValue = useMemo(
    () => formatStatValue(value, formatOptions, prefix, suffix),
    [value, formatOptions, prefix, suffix]
  );

  return (
    <>
      {icon ? (
        <div className="mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-muted/50">
          {icon}
        </div>
      ) : null}
      <span className={cn("text-foreground tabular-nums", valueClassName)}>
        {staticValue}
      </span>
      <span className={cn("mt-0.5 text-chart-label", labelClassName)}>
        {label}
      </span>
    </>
  );
}

ChartStatFlow.displayName = "ChartStatFlow";
