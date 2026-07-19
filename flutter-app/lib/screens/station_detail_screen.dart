import 'package:flutter/material.dart';
import '../config/theme.dart';
import '../models/station.dart';
import '../services/api_service.dart';
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
      ]);
      setState(() {
        _station = results[0] as Station?;
        _prices = results[1] as List<Map<String, dynamic>>;
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
          child:
              Text('Станция не найдена', style: TextStyle(color: AppTheme.muted)),
        ),
      );
    }

    return Scaffold(
      appBar: AppBar(
        title: Text(_station!.operator ?? _station!.name,
            maxLines: 1, overflow: TextOverflow.ellipsis),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _buildHeader(),
            const SizedBox(height: 16),
            _buildStatuses(),
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
        label: const Text('Сообщить', style: TextStyle(color: Colors.white)),
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
              if (_station!.isVerified) ...[
                const Icon(Icons.verified, color: AppTheme.info, size: 16),
                const SizedBox(width: 4),
              ],
              if (_station!.distance != null)
                Text(
                  _station!.distance! < 1
                      ? '${(_station!.distance! * 1000).round()} м'
                      : '${_station!.distance!.toStringAsFixed(1)} км',
                  style:
                      const TextStyle(color: AppTheme.muted, fontSize: 13),
                ),
            ],
          ),
          if (_station!.address != null && _station!.address!.isNotEmpty) ...[
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

  Widget _buildStatuses() {
    if (_station!.statuses.isEmpty) {
      return Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: AppTheme.bgCard,
          borderRadius: BorderRadius.circular(16),
        ),
        child: const Center(
          child: Text('Нет данных о наличии',
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
          const Text('Топливо и цены',
              style: TextStyle(
                color: AppTheme.textPrimary,
                fontSize: 16,
                fontWeight: FontWeight.w600,
              )),
          const SizedBox(height: 12),
          ..._station!.statuses.map((s) {
            final has = s.available == true;
            final no = s.available == false;
            final price =
                s.price != null ? '${s.price!.toStringAsFixed(2)} ₽' : '';
            final statusText =
                has ? 'В наличии' : no ? 'Нет в наличии' : 'Нет данных';
            final statusColor =
                has ? AppTheme.success : no ? AppTheme.danger : AppTheme.muted;

            return Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Row(
                children: [
                  Icon(
                    has ? Icons.check_circle : no ? Icons.cancel : Icons.help_outline,
                    size: 16,
                    color: statusColor,
                  ),
                  const SizedBox(width: 8),
                  Text(
                    _fuelLabel(s.fuelType),
                    style: const TextStyle(
                      color: AppTheme.textPrimary,
                      fontSize: 14,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                  const Spacer(),
                  if (price.isNotEmpty)
                    Text(
                      price,
                      style: const TextStyle(
                        color: AppTheme.textPrimary,
                        fontSize: 14,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  const SizedBox(width: 12),
                  Text(
                    statusText,
                    style: TextStyle(
                      color: statusColor,
                      fontSize: 12,
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
                          color: AppTheme.textSecondary, fontSize: 13),
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
                          color: AppTheme.info, fontSize: 11),
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
        stationName: _station?.operator ?? _station?.name ?? '',
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
}
