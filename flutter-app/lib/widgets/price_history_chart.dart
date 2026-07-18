import 'package:flutter/material.dart';
import '../config/theme.dart';

class PriceHistoryChart extends StatelessWidget {
  final List<Map<String, dynamic>> history;

  const PriceHistoryChart({super.key, required this.history});

  @override
  Widget build(BuildContext context) {
    if (history.isEmpty) {
      return const Center(
        child: Text('Нет данных', style: TextStyle(color: AppTheme.muted)),
      );
    }

    final prices = <double>[
      for (final e in history)
        if ((e['price'] ?? 0) > 0) (e['price'] as num).toDouble(),
    ];

    if (prices.isEmpty) {
      return const Center(
        child: Text('Нет данных', style: TextStyle(color: AppTheme.muted)),
      );
    }

    final minPrice = prices.reduce((a, b) => a < b ? a : b);
    final maxPrice = prices.reduce((a, b) => a > b ? a : b);
    final range = maxPrice - minPrice;

    return CustomPaint(
      size: Size.infinite,
      painter: _ChartPainter(prices: prices, minPrice: minPrice, range: range),
    );
  }
}

class _ChartPainter extends CustomPainter {
  final List<double> prices;
  final double minPrice;
  final double range;

  _ChartPainter({
    required this.prices,
    required this.minPrice,
    required this.range,
  });

  @override
  void paint(Canvas canvas, Size size) {
    if (prices.length < 2) return;

    final paint = Paint()
      ..color = AppTheme.accent
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    final fillPaint = Paint()
      ..shader = const LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [
          Color(0x40ff6b35),
          Color(0x00ff6b35),
        ],
      ).createShader(Rect.fromLTWH(0, 0, size.width, size.height));

    final path = Path();
    final fillPath = Path();

    final step = size.width / (prices.length - 1);
    final padding = 8.0;

    for (int i = 0; i < prices.length; i++) {
      final x = i * step;
      final normalized = range > 0
          ? (prices[i] - minPrice) / range
          : 0.5;
      final y = size.height - padding - normalized * (size.height - padding * 2);

      if (i == 0) {
        path.moveTo(x, y);
        fillPath.moveTo(x, size.height);
        fillPath.lineTo(x, y);
      } else {
        path.lineTo(x, y);
        fillPath.lineTo(x, y);
      }
    }

    fillPath.lineTo(size.width, size.height);
    fillPath.close();

    canvas.drawPath(fillPath, fillPaint);
    canvas.drawPath(path, paint);

    final dotPaint = Paint()
      ..color = AppTheme.accent
      ..style = PaintingStyle.fill;

    final lastX = (prices.length - 1) * step;
    final lastNormalized = range > 0
        ? (prices.last - minPrice) / range
        : 0.5;
    final lastY =
        size.height - padding - lastNormalized * (size.height - padding * 2);

    canvas.drawCircle(Offset(lastX, lastY), 4, dotPaint);
  }

  @override
  bool shouldRepaint(covariant _ChartPainter old) {
    return old.prices != prices;
  }
}
