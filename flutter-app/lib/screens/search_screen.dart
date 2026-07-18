import 'package:flutter/material.dart';
import '../config/api.dart';
import '../config/theme.dart';
import '../models/station.dart';
import '../services/api_service.dart';
import '../services/storage_service.dart';
import '../widgets/station_card.dart';
import 'station_detail_screen.dart';

class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key});

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final ApiService _api = ApiService();
  final StorageService _storage = StorageService();
  final TextEditingController _searchController = TextEditingController();

  List<Station> _stations = [];
  bool _isLoading = false;
  String _selectedCity = '';
  String _selectedFuel = '95';
  String _selectedNetwork = '';
  double? _maxPrice;

  @override
  void initState() {
    super.initState();
    _selectedCity = _storage.selectedCity ?? 'Иваново';
  }

  Future<void> _search(String query) async {
    if (query.trim().isEmpty) return;
    setState(() => _isLoading = true);
    try {
      final stations = await _api.searchStations(query);
      setState(() {
        _stations = stations;
        _isLoading = false;
      });
    } catch (_) {
      setState(() => _isLoading = false);
    }
  }

  Future<void> _loadCity(String city) async {
    setState(() {
      _isLoading = true;
      _selectedCity = city;
    });
    _storage.selectedCity = city;
    try {
      final stations = await _api.getStationsByCity(
        city: city,
        fuel: _selectedFuel,
        network: _selectedNetwork.isNotEmpty ? _selectedNetwork : null,
        maxPrice: _maxPrice,
      );
      setState(() {
        _stations = stations;
        _isLoading = false;
      });
    } catch (_) {
      setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Поиск АЗС'),
      ),
      body: Column(
        children: [
          _buildSearchBar(),
          _buildCitySelector(),
          _buildFilters(),
          Expanded(
            child: _isLoading
                ? const Center(
                    child: CircularProgressIndicator(color: AppTheme.accent))
                : _stations.isEmpty
                    ? _buildEmptyState()
                    : _buildStationList(),
          ),
        ],
      ),
    );
  }

  Widget _buildSearchBar() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
      child: TextField(
        controller: _searchController,
        style: const TextStyle(color: AppTheme.textPrimary),
        decoration: InputDecoration(
          hintText: 'Поиск станции...',
          hintStyle: TextStyle(color: AppTheme.muted),
          prefixIcon:
              const Icon(Icons.search, color: AppTheme.muted),
          suffixIcon: _searchController.text.isNotEmpty
              ? IconButton(
                  icon: const Icon(Icons.clear, color: AppTheme.muted),
                  onPressed: () {
                    _searchController.clear();
                    _loadCity(_selectedCity);
                    setState(() {});
                  },
                )
              : null,
        ),
        onChanged: (value) {
          setState(() {});
          if (value.length >= 3) _search(value);
        },
        onSubmitted: _search,
      ),
    );
  }

  Widget _buildCitySelector() {
    return Container(
      height: 48,
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: ListView(
        scrollDirection: Axis.horizontal,
        children: ApiConfig.cities.take(20).map((city) {
          final isSelected = _selectedCity == city;
          return Padding(
            padding: const EdgeInsets.only(right: 8),
            child: FilterChip(
              label: Text(city),
              selected: isSelected,
              onSelected: (_) => _loadCity(city),
              selectedColor: AppTheme.accent,
              checkmarkColor: Colors.white,
              labelStyle: TextStyle(
                color: isSelected ? Colors.white : AppTheme.textSecondary,
                fontSize: 13,
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _buildFilters() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Row(
        children: [
          _buildFuelDropdown(),
          const SizedBox(width: 8),
          _buildNetworkDropdown(),
          const Spacer(),
          _buildPriceFilter(),
        ],
      ),
    );
  }

  Widget _buildFuelDropdown() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(8),
      ),
      child: DropdownButton<String>(
        value: _selectedFuel,
        dropdownColor: AppTheme.bgCard,
        underline: const SizedBox(),
        isDense: true,
        style: const TextStyle(color: AppTheme.textPrimary, fontSize: 13),
        items: const [
          DropdownMenuItem(value: '92', child: Text('АИ-92')),
          DropdownMenuItem(value: '95', child: Text('АИ-95')),
          DropdownMenuItem(value: '98', child: Text('АИ-98')),
          DropdownMenuItem(value: 'diesel', child: Text('ДТ')),
          DropdownMenuItem(value: 'lpg', child: Text('Газ')),
        ],
        onChanged: (v) {
          if (v != null) {
            setState(() => _selectedFuel = v);
            _loadCity(_selectedCity);
          }
        },
      ),
    );
  }

  Widget _buildNetworkDropdown() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(8),
      ),
      child: DropdownButton<String>(
        value: _selectedNetwork.isEmpty ? null : _selectedNetwork,
        hint: const Text('Сеть',
            style: TextStyle(color: AppTheme.muted, fontSize: 13)),
        dropdownColor: AppTheme.bgCard,
        underline: const SizedBox(),
        isDense: true,
        style: const TextStyle(color: AppTheme.textPrimary, fontSize: 13),
        items: const [
          DropdownMenuItem(value: '', child: Text('Все сети')),
          DropdownMenuItem(value: 'Лукойл', child: Text('Лукойл')),
          DropdownMenuItem(value: 'Газпром', child: Text('Газпром')),
          DropdownMenuItem(value: 'Роснефть', child: Text('Роснефть')),
          DropdownMenuItem(value: 'Татнефть', child: Text('Татнефть')),
          DropdownMenuItem(value: 'Башнефть', child: Text('Башнефть')),
        ],
        onChanged: (v) {
          setState(() => _selectedNetwork = v ?? '');
          _loadCity(_selectedCity);
        },
      ),
    );
  }

  Widget _buildPriceFilter() {
    return GestureDetector(
      onTap: () => _showPriceFilter(),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: AppTheme.bgCard,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.attach_money,
                size: 16,
                color:
                    _maxPrice != null ? AppTheme.accent : AppTheme.muted),
            const SizedBox(width: 4),
            Text(
              _maxPrice != null ? 'до ${_maxPrice!.toInt()}₽' : 'Цена',
              style: TextStyle(
                color:
                    _maxPrice != null ? AppTheme.accent : AppTheme.muted,
                fontSize: 13,
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _showPriceFilter() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppTheme.bgCard,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('Максимальная цена',
                style: TextStyle(
                    color: AppTheme.textPrimary,
                    fontSize: 18,
                    fontWeight: FontWeight.w600)),
            const SizedBox(height: 16),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [null, 70.0, 80.0, 90.0, 100.0].map((price) {
                final isSelected = _maxPrice == price;
                return ChoiceChip(
                  label: Text(price == null ? 'Любая' : 'до ${price.toInt()}₽'),
                  selected: isSelected,
                  selectedColor: AppTheme.accent,
                  onSelected: (_) {
                    setState(() => _maxPrice = price);
                    Navigator.pop(ctx);
                    _loadCity(_selectedCity);
                  },
                );
              }).toList(),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.local_gas_station_outlined,
              size: 64, color: AppTheme.muted.withValues(alpha: 0.3)),
          const SizedBox(height: 16),
          Text(
            'Выбери город или поищи станцию',
            style: TextStyle(color: AppTheme.muted, fontSize: 16),
          ),
        ],
      ),
    );
  }

  Widget _buildStationList() {
    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      itemCount: _stations.length,
      itemBuilder: (ctx, i) {
        final station = _stations[i];
        return StationCard(
          station: station,
          selectedFuel: _selectedFuel,
          onTap: () => Navigator.push(
            ctx,
            MaterialPageRoute(
              builder: (_) => StationDetailScreen(stationId: station.id),
            ),
          ),
        );
      },
    );
  }
}
