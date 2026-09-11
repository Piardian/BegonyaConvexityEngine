from dataclasses import dataclass
from typing import List
import numpy as np
from engine.convexity_simulator import ConvexityTrade

@dataclass
class ConvexityReport:
    name: str
    total_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    total_r: float
    total_pnl_dollars: float
    return_on_capital_pct: float
    profit_factor: float
    avg_win_r: float
    avg_loss_r: float
    payoff_ratio: float
    expectancy_r: float
    max_drawdown_r: float
    max_drawdown_pct: float
    max_consecutive_losses: int
    avg_holding_days: float
    trades: List[ConvexityTrade]

class PerformanceReporter:
    @staticmethod
    def calculate(trades: List[ConvexityTrade], name: str = "Portfolio", initial_capital: float = 10000.0) -> ConvexityReport:
        if not trades:
            return ConvexityReport(
                name=name, total_trades=0, wins=0, losses=0, win_rate_pct=0.0,
                total_r=0.0, total_pnl_dollars=0.0, return_on_capital_pct=0.0,
                profit_factor=0.0, avg_win_r=0.0, avg_loss_r=0.0, payoff_ratio=0.0,
                expectancy_r=0.0, max_drawdown_r=0.0, max_drawdown_pct=0.0,
                max_consecutive_losses=0, avg_holding_days=0.0, trades=[]
            )

        total_trades = len(trades)
        win_trades = [t for t in trades if t.pnl_r > 0]
        loss_trades = [t for t in trades if t.pnl_r <= 0]

        wins = len(win_trades)
        losses = len(loss_trades)
        win_rate_pct = round((wins / total_trades) * 100, 1)

        total_r = round(sum(t.pnl_r for t in trades), 2)
        total_pnl_dollars = round(sum(t.pnl_dollars for t in trades), 2)
        return_on_capital_pct = round((total_pnl_dollars / initial_capital) * 100, 2)

        win_sum_r = sum(t.pnl_r for t in win_trades)
        loss_sum_r = abs(sum(t.pnl_r for t in loss_trades))
        profit_factor = round(win_sum_r / loss_sum_r, 2) if loss_sum_r > 0 else 999.0

        avg_win_r = round(win_sum_r / wins, 2) if wins > 0 else 0.0
        avg_loss_r = round(loss_sum_r / losses, 2) if losses > 0 else 0.0
        payoff_ratio = round(avg_win_r / avg_loss_r, 2) if avg_loss_r > 0 else 999.0

        expectancy_r = round((win_rate_pct / 100.0 * avg_win_r) - ((1.0 - win_rate_pct / 100.0) * avg_loss_r), 2)

        # Drawdown ve Art Arda Kayıp Hesaplama
        equity_curve_r = [0.0]
        curr_r = 0.0
        peak_r = 0.0
        max_dd_r = 0.0

        consec_losses = 0
        max_consec_losses = 0

        for t in trades:
            curr_r += t.pnl_r
            equity_curve_r.append(curr_r)
            if curr_r > peak_r:
                peak_r = curr_r
            dd = peak_r - curr_r
            if dd > max_dd_r:
                max_dd_r = dd

            if t.pnl_r <= 0:
                consec_losses += 1
                if consec_losses > max_consec_losses:
                    max_consec_losses = consec_losses
            else:
                consec_losses = 0

        max_dd_r = round(max_dd_r, 2)
        # %0.5 risk ile sermaye drawdown'ı
        max_dd_pct = round(max_dd_r * 0.5, 2)

        avg_holding_days = round(sum(t.holding_days for t in trades) / total_trades, 1)

        return ConvexityReport(
            name=name,
            total_trades=total_trades,
            wins=wins,
            losses=losses,
            win_rate_pct=win_rate_pct,
            total_r=total_r,
            total_pnl_dollars=total_pnl_dollars,
            return_on_capital_pct=return_on_capital_pct,
            profit_factor=profit_factor,
            avg_win_r=avg_win_r,
            avg_loss_r=avg_loss_r,
            payoff_ratio=payoff_ratio,
            expectancy_r=expectancy_r,
            max_drawdown_r=max_dd_r,
            max_drawdown_pct=max_dd_pct,
            max_consecutive_losses=max_consec_losses,
            avg_holding_days=avg_holding_days,
            trades=trades
        )

    @staticmethod
    def render_ascii_curve(trades: List[ConvexityTrade], height: int = 10, width: int = 50) -> str:
        if not trades:
            return "  [Veri Yok]"

        cum_r = [0.0]
        cur = 0.0
        for t in trades:
            cur += t.pnl_r
            cum_r.append(cur)

        indices = np.linspace(0, len(cum_r) - 1, width).astype(int)
        sampled = [cum_r[i] for i in indices]

        min_val = min(sampled)
        max_val = max(sampled)
        val_range = max_val - min_val if max_val != min_val else 1.0

        grid = [[" " for _ in range(width)] for _ in range(height)]
        zero_row = int(round((max_val - 0.0) / val_range * (height - 1)))
        if 0 <= zero_row < height:
            for c in range(width):
                grid[zero_row][c] = "─"

        for col, val in enumerate(sampled):
            row = int(round((max_val - val) / val_range * (height - 1)))
            row = max(0, min(height - 1, row))
            grid[row][col] = "●" if col == width - 1 else "▪"

        lines = []
        for r in range(height):
            y_val = max_val - (r / (height - 1)) * val_range
            prefix = f"{y_val:>+6.1f} R │ "
            lines.append(prefix + "".join(grid[r]))

        lines.append("       └" + "─" * width)
        lines.append(f"        Başlangıç {' ' * (width - 24)} {len(trades)} İşlem Sonu ({cum_r[-1]:>+5.1f}R)")
        return "\n".join(lines)
