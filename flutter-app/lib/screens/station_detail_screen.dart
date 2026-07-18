import 'package:flutter/material.dart';
import '../config/theme.dart';
import '../models/station.dart';
import '../services/api_service.dart';
import '../widgets/price_history_chart.dart';
import '../widgets/report_sheet.dart';

class StationDetailScreen extends StatefulWidget {
  final int stationId;

  const StationDetailScreen({super.key, required this.stationId});

  @override
  State<StationDetailScreen> createState() => _StationDetailScreenState();
}

class _StationDetailScreenState extends State<StationDetailScreen> {
  final ApiService _api = ApiService();
  Station? _station;
  List<Map<String, dynamic>> _prices = [];
  List<Map<String, dynamic>> _history = [];
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _loadData();
  }

  Future<void> _loadData() async {
    try {
      final results = await Future.wait([
        _api.getStationDetail(widget.stationId),
        _api.getStationPrices(widget.stationId),
        _api.getPriceHistory(widget.stationId, fuel: '95', days: 30),
      ]);
      setState(() {
        _station = results[0] as Station?;
        _prices = results[1] as List<Map<String, dynamic>>;
        _history = results[2] as List<Map<String, dynamic>>;
        _isLoading = false;
      });
    } catch (_) {
      setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_isLoading) {
      return Scaffold(
        appBar: AppBar(title: const Text('Загрузка...')),
        body: const Center(
          child: CircularProgressIndicator(color: AppTheme.accent),
        ),
      );
    }

    if (_station == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('Ошибка')),
        body: const Center(
          child: Text('Станция не найдена',
              style: TextStyle(color: AppTheme.muted)),
        ),
      );
    }

    return Scaffold(
      appBar: AppBar(
        title: Text(_station!.name, maxLines: 1, overflow: TextOverflow.ellipsis),
        actions: [
          IconButton(
            icon: const Icon(Icons.share_outlined),
            onPressed: _shareStation,
          ),
        ],
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _buildHeader(),
            const SizedBox(height: 16),
            _buildPriceSection(),
            const SizedBox(height: 16),
            _buildFuelAvailability(),
            if (_history.isNotEmpty) ...[
              const SizedBox(height: 16),
              _buildPriceHistory(),
            ],
            if (_prices.isNotEmpty) ...[
              const SizedBox(height: 16),
              _buildPriceSources(),
            ],
          ],
        ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: AppTheme.accent,
        onPressed: _showReportSheet,
        icon: const Icon(Icons.edit, color: Colors.white),
        label: const Text('Сообщить',
            style: TextStyle(color: Colors.white)),
      ),
    );
  }

  Widget _buildHeader() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              if (_station!.network != null) ...[
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(
                    color: AppTheme.accent.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Text(
                    _station!.network!,
                    style: const TextStyle(
                      color: AppTheme.accent,
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
              ],
              if (_station!.distance != null)
                Text(
                  _formatDistance(_station!.distance!),
                  style: const TextStyle(color: AppTheme.muted, fontSize: 13),
                ),
            ],
          ),
          if (_station!.address != null) ...[
            const SizedBox(height: 8),
            Row(
              children: [
                const Icon(Icons.location_on_outlined,
                    size: 16, color: AppTheme.muted),
                const SizedBox(width: 4),
                Expanded(
                  child: Text(
                    _station!.address!,
                    style: const TextStyle(
                        color: AppTheme.textSecondary, fontSize: 13),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildPriceSection() {
    if (_station!.prices.isEmpty) {
      return Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: AppTheme.bgCard,
          borderRadius: BorderRadius.circular(16),
        ),
        child: const Center(
          child: Text('Цены пока неизвестны',
              style: TextStyle(color: AppTheme.muted)),
        ),
      );
    }

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Цены на топливо',
              style: TextStyle(
                color: AppTheme.textPrimary,
                fontSize: 16,
                fontWeight: FontWeight.w600,
              )),
          const SizedBox(height: 12),
          Wrap(
            spacing: 12,
            runSpacing: 8,
            children: _station!.prices.entries.map((e) {
              return _buildPriceChip(e.key, e.value);
            }).toList(),
          ),
        ],
      ),
    );
  }

  Widget _buildPriceChip(String fuel, FuelPrice price) {
    final fuelName = _fuelLabel(fuel);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: AppTheme.bgCardLight,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        children: [
          Text(fuelName,
              style: const TextStyle(color: AppTheme.muted, fontSize: 11)),
          const SizedBox(height: 4),
          Text(price.priceText,
              style: const TextStyle(
                color: AppTheme.textPrimary,
                fontSize: 16,
                fontWeight: FontWeight.w700,
              )),
        ],
      ),
    );
  }

  Widget _buildFuelAvailability() {
    if (_station!.availability.isEmpty) return const SizedBox.shrink();

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Наличие',
              style: TextStyle(
                color: AppTheme.textPrimary,
                fontSize: 16,
                fontWeight: FontWeight.w600,
              )),
          const SizedBox(height: 12),
          ..._station!.availability.entries.map((e) {
            return Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(
                children: [
                  Icon(
                    _statusIcon(e.value),
                    size: 16,
                    color: _statusColor(e.value),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    _fuelLabel(e.key),
                    style: const TextStyle(
                      color: AppTheme.textSecondary,
                      fontSize: 14,
                    ),
                  ),
                  const Spacer(),
                  Text(
                    _statusText(e.value),
                    style: TextStyle(
                      color: _statusColor(e.value),
                      fontSize: 13,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ],
              ),
            );
          }),
        ],
      ),
    );
  }

  Widget _buildPriceHistory() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Text('История цен',
                  style: TextStyle(
                    color: AppTheme.textPrimary,
                    fontSize: 16,
                    fontWeight: FontWeight.w600,
                  )),
              const Spacer(),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: AppTheme.premium.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: const Text('PREMIUM',
                    style: TextStyle(
                      color: AppTheme.premium,
                      fontSize: 10,
                      fontWeight: FontWeight.w700,
                    )),
              ),
            ],
          ),
          const SizedBox(height: 12),
          SizedBox(
            height: 120,
            child: PriceHistoryChart(history: _history),
          ),
        ],
      ),
    );
  }

  Widget _buildPriceSources() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Источники цен',
              style: TextStyle(
                color: AppTheme.textPrimary,
                fontSize: 16,
                fontWeight: FontWeight.w600,
              )),
          const SizedBox(height: 12),
          ..._prices.map((p) {
            return Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      '${_fuelLabel(p['fuel_type']?.toString() ?? '')} — ${p['price'] ?? '—'} ₽',
                      style: const TextStyle(
                        color: AppTheme.textSecondary,
                        fontSize: 13,
                      ),
                    ),
                  ),
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(
                      color: AppTheme.info.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text(
                      p['source']?.toString() ?? '—',
                      style: const TextStyle(
                        color: AppTheme.info,
                        fontSize: 11,
                      ),
                    ),
                  ),
                ],
              ),
            );
          }),
        ],
      ),
    );
  }

  void _showReportSheet() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppTheme.bgCard,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (_) => ReportSheet(
        stationId: widget.stationId,
        stationName: _station?.name ?? '',
      ),
    );
  }

  void _shareStation() {
    // TODO: use share_plus
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

  IconData _statusIcon(String status) {
    switch (status) {
      case 'in_stock':
        return Icons.check_circle;
      case 'partial':
        return Icons.warning_amber;
      case 'out_of_stock':
        return Icons.cancel;
      default:
        return Icons.help_outline;
    }
  }

  Color _statusColor(String status) {
    switch (status) {
      case 'in_stock':
        return AppTheme.success;
      case 'partial':
        return AppTheme.warning;
      case 'out_of_stock':
        return AppTheme.danger;
      default:
        return AppTheme.muted;
    }
  }

  String _statusText(String status) {
    switch (status) {
      case 'in_stock':
        return 'В наличии';
      case 'partial':
        return 'Осталось мало';
      case 'out_of_stock':
        return 'Нет в наличии';
      default:
        return 'Нет данных';
    }
  }

  String _formatDistance(double meters) {
    if (meters < 1000) return '${meters.round()} м';
    return '${(meters / 1000).toStringAsFixed(1)} км';
  }
}
