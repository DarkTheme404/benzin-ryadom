import 'package:flutter/material.dart';
import '../config/theme.dart';
import '../models/station.dart';

class StationCard extends StatelessWidget {
  final Station station;
  final String selectedFuel;
  final VoidCallback? onTap;

  const StationCard({
    super.key,
    required this.station,
    required this.selectedFuel,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: AppTheme.bgCard,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _buildHeader(),
            const SizedBox(height: 10),
            _buildPriceRow(),
            const SizedBox(height: 8),
            _buildFooter(),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader() {
    return Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                station.name,
                style: const TextStyle(
                  color: AppTheme.textPrimary,
                  fontSize: 15,
                  fontWeight: FontWeight.w600,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              if (station.address != null) ...[
                const SizedBox(height: 4),
                Text(
                  station.address!,
                  style: const TextStyle(
                    color: AppTheme.muted,
                    fontSize: 12,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ],
          ),
        ),
        if (station.distance != null) ...[
          const SizedBox(width: 8),
          _buildDistanceBadge(),
        ],
      ],
    );
  }

  Widget _buildPriceRow() {
    final price = station.prices[selectedFuel];
    if (price == null || price.price == null) {
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: AppTheme.bgCardLight,
          borderRadius: BorderRadius.circular(8),
        ),
        child: const Text(
          'Цена неизвестна',
          style: TextStyle(color: AppTheme.muted, fontSize: 13),
        ),
      );
    }

    return Row(
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: AppTheme.success.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Row(
            children: [
              Text(
                _fuelLabel(selectedFuel),
                style: const TextStyle(
                  color: AppTheme.muted,
                  fontSize: 12,
                ),
              ),
              const SizedBox(width: 8),
              Text(
                price.priceText,
                style: const TextStyle(
                  color: AppTheme.textPrimary,
                  fontSize: 16,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildFooter() {
    return Row(
      children: [
        _buildStatusChip(),
        const Spacer(),
        if (station.reportCount != null)
          Row(
            children: [
              const Icon(Icons.assessment, size: 12, color: AppTheme.muted),
              const SizedBox(width: 4),
              Text(
                '${station.reportCount}',
                style: const TextStyle(color: AppTheme.muted, fontSize: 11),
              ),
            ],
          ),
        if (station.lastUpdate != null) ...[
          const SizedBox(width: 12),
          Text(
            _formatAge(station.lastUpdate!),
            style: const TextStyle(color: AppTheme.muted, fontSize: 11),
          ),
        ],
      ],
    );
  }

  Widget _buildStatusChip() {
    final status = station.fuelStatus;
    Color color;
    String text;
    switch (status) {
      case 'in_stock':
        color = AppTheme.success;
        text = 'В наличии';
        break;
      case 'partial':
        color = AppTheme.warning;
        text = 'Осталось мало';
        break;
      case 'out_of_stock':
        color = AppTheme.danger;
        text = 'Нет';
        break;
      default:
        color = AppTheme.muted;
        text = 'Нет данных';
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        text,
        style: TextStyle(
          color: color,
          fontSize: 11,
          fontWeight: FontWeight.w500,
        ),
      ),
    );
  }

  Widget _buildDistanceBadge() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: AppTheme.info.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        _formatDistance(station.distance!),
        style: const TextStyle(
          color: AppTheme.info,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }

  String _fuelLabel(String fuel) {
    switch (fuel) {
      case '92':
        return 'АИ-92';
      case '95':
        return 'АИ-95';
      case '98':
        return 'АИ-98';
      case 'diesel':
        return 'ДТ';
      case 'lpg':
        return 'Газ';
      default:
        return fuel;
    }
  }

  String _formatDistance(double meters) {
    if (meters < 1000) return '${meters.round()} м';
    return '${(meters / 1000).toStringAsFixed(1)} км';
  }

  String _formatAge(String dateStr) {
    try {
      final date = DateTime.parse(dateStr);
      final diff = DateTime.now().difference(date);
      if (diff.inMinutes < 60) return '${diff.inMinutes} мин назад';
      if (diff.inHours < 24) return '${diff.inHours} ч назад';
      return '${diff.inDays} дн назад';
    } catch (_) {
      return '';
    }
  }
}
